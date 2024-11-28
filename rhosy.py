import re, math, mido, queue, sounddevice
import numpy as np

# Print MIDI note events if True.
log_notes = True

# Print envelope events if True.
log_envelope = True

# Sample rate in sps. This doesn't need to be fixed: it
# could be set to the preferred rate of the audio output.
sample_rate = 48000

# Blocksize in samples to process. My desktop machine keeps
# up at this rate, which provides pretty good latency. Slower
# machines may need larger numbers.
blocksize = 16

# Pick a MIDI controller.
controllers = {
    'USB Oxygen 8 v2 MIDI 1',
}
input_name_re = re.compile(r"[^:]*:(.*) [0-9]*:[0-9]*")
inputs = mido.get_input_names()
controller_name = None
for input_name in inputs:
    m = input_name_re.fullmatch(input_name)
    if m is None:
        continue
    name = m[1]
    if name in controllers:
        controller_name = name
        break
if controller_name is None:
    print("No controller")
    exit(1)
controller = mido.open_input(controller_name)

# Return a sine wave of frequency f.
def make_sin(f):
    period = sample_rate / f
    # Need enough cycles to be able to wrap around when
    # generating a block.
    ncycles = math.ceil(blocksize / period)
    nsin = round(ncycles * period)
    t_period = np.linspace(0, ncycles * (2 * np.pi), nsin, dtype=np.float32)
    # Allow for eight notes before clipping.
    return 0.125 * np.sin(t_period)

# Precalculate wave tables
notes = []
for note in range(128):
    f = 440 * 2 ** ((note - 69) / 12)
    notes.append(make_sin(f))

class Note:
    def __init__(self, key):
        self.t = 0
        self.key = key
        self.release_rate = None
        # Hardwire to 20ms for now.
        attack_samples = 10 * sample_rate / 1000
        self.attack_rate = 1.0 / attack_samples
        self.attack_amplitude = 0
        self.wave_table = notes[key]
        self.held = False
    
    # Returns a requested block of samples.
    def play(self, frame_count):
        # Cache some state.
        wave_table = self.wave_table
        t_output = self.t

        # Wrap the output as needed.
        nwave_table = len(wave_table)
        t_start = t_output % nwave_table
        t_end = (t_output + frame_count) % nwave_table
        if t_start < t_end:
            output = wave_table[t_start:t_end]
        else:
            output = np.append(wave_table[t_start:], wave_table[:t_end])

        # Handle release as needed.
        if self.release_rate and not self.held:
            if self.release_amplitude <= 0:
                if log_envelope:
                    print("finishing note", self.key, self.t)
                return None
            end_amplitude = \
                self.release_amplitude - frame_count * self.release_rate
            scale = np.linspace(
                self.release_amplitude,
                end_amplitude,
                frame_count,
            ).clip(0, 1)
            output = output * scale
            self.release_amplitude = np.max(end_amplitude, 0)
            
        # Handle attack as needed.
        if self.attack_rate:
            end_amplitude = \
                self.attack_amplitude + frame_count * self.attack_rate
            scale = np.linspace(
                self.attack_amplitude,
                end_amplitude,
                frame_count,
            ).clip(0, 1)
            output = output * scale
            if end_amplitude >= 1:
                if log_envelope:
                    print("finishing attack", self.key, self.t)
                self.attack_rate = None
            else:
                self.attack_amplitude = end_amplitude

        # Get the samples.
        self.t += frame_count
        return output

    # Mark the note as released and start the release timer.
    def release(self):
        if log_envelope:
            print("releasing note", self.key, self.t)
        # Hardcode release time to 100ms
        release_samples = 100 * sample_rate / 1000
        self.release_rate = 1.0 / release_samples
        self.release_amplitude = 1.0
        if self.attack_rate:
            self.release_amplitude = self.attack_amplitude
            self.attack_rate = None

    # Mark the current note as held by sustain pedal.
    def hold(self):
        self.held = True
    
    # Mark the current note as no longer held by sustain pedal.
    def unhold(self):
        self.held = False

# Currently playing notes.
current_notes = dict()

# Queue of MIDI messages for state changes.
command_queue = queue.SimpleQueue()

# Sustain pedal is held.
sustaining = False

# This callback is called by `sounddevice` to get some
# samples to output. It's the heart of sound generation in
# the synth.
def output_callback(out_data, frame_count, time_info, status):
    global current_notes, command_queue, sustaining

    # A non-None status indicates that something has
    # happened with sound output that shouldn't have.  This
    # is almost always an underrun due to generating samples
    # too slowly.
    if status:
        print("output callback:", status)

    while not command_queue.empty():
        mesg_type, mesg = command_queue.get()
        if mesg_type == 'note_on':
            key = mesg.note
            new_note = Note(key)
            if sustaining:
                new_note.held = True
            current_notes[key] = new_note
        elif mesg_type == 'note_off':
            key = mesg.note
            current_notes[key].release()
        elif mesg_type == 'sustain_pedal':
            sustaining = mesg.value > 0
            for note in current_notes.values():
                if sustaining:
                    note.hold()
                else:
                    note.unhold()
        else:
            raise Exception(f"bad message in command queue: {mesg_type} {mesg}")

    # Mix samples from notes.
    output = np.zeros(frame_count, dtype = np.float32)
    finished_keys = []
    for key, note in current_notes.items():
        sound = note.play(frame_count)
        if sound is None:
            finished_keys.append(key)
        else:
            output += sound

    # Remove finished notes.
    for key in finished_keys:
        del current_notes[key]

    # Note that we need the out_data slicing to *replace*
    # the data in the array.
    out_data[:] = output.reshape(frame_count,1)

# Start audio playing. Must keep up with output from here on.
output_stream = sounddevice.OutputStream(
    samplerate=sample_rate,
    channels=1,
    blocksize=blocksize,
    callback=output_callback,
)
output_stream.start()

# Block waiting for the controller (keyboard) to send a MIDI
# message, then handle it. Return False if the MIDI message
# wants the instrument (synthesizer) to stop, True otherwise.
def get_midi_event(controller):
    # Block until a MIDI message is received.
    mesg = controller.receive()

    # Select what to do based on message type.
    mesg_type = mesg.type
    # Special case: note on with velocity 0 indicates
    # note off (for older MIDI instruments).
    if mesg_type == 'note_on' and mesg.velocity == 0:
        mesg_type = 'note_off'
    # Add a note to the sound. If it is already on just
    # start it again.
    if mesg_type == 'note_on':
        key = mesg.note
        velocity = mesg.velocity / 127
        if log_notes:
            print('note on', key, mesg.velocity, round(velocity, 2))
        command_queue.put((mesg_type, mesg))
    # Remove a note from the sound. If it is already off,
    # this message will be ignored.
    elif mesg_type == 'note_off':
        key = mesg.note
        velocity = round(mesg.velocity / 127, 2)
        if log_notes:
            print('note off', key, mesg.velocity, velocity)
        command_queue.put((mesg_type, mesg))
    # Handle various controls.
    elif mesg.type == 'control_change':
        # XXX Hard-wired for "stop" key on Oxygen8.
        if mesg.control == 23:
            print('stop')
            return False
        # Change output waveform.
        #
        # XXX Hard-wired for "fast-forward" and "reverse"
        # keys on Oxygen8. Hard-coded for exactly two possible
        # waveforms.
        elif mesg.control == 21 or mesg.control == 22:
            print('program change')
            #out_osc = (out_osc + 1) % 2
        # Sustain pedal press / release.
        elif mesg.control == 64:
            print('sustain pedal', mesg.value)
            command_queue.put(('sustain_pedal', mesg))
        # Unknown control changes are logged and ignored.
        else:
            print(f"control", mesg.control, mesg.value)
    # XXX Pitchwheel is currently logged and ignored.
    elif mesg.type == 'pitchwheel':
        pitch = round(mesg.pitch / 127, 2)
        print('pitchwheel', mesg.pitch, pitch)
    else:
        print('unknown MIDI message', mesg)
    return True

# Run the instrument until the controller stop key is pressed.
while get_midi_event(controller):
    pass
