import math, mido, sounddevice
import numpy as np

# Print MIDI note events if True.
log_notes = True

# Sample rate in sps. This doesn't need to be fixed: it
# could be set to the preferred rate of the audio output.
sample_rate = 48000

# Blocksize in samples to process. My desktop machine keeps
# up at this rate, which provides pretty good latency. Slower
# machines may need larger numbers.
blocksize = 16

# MIDI controller is currently hardwired.
controller = mido.open_input('USB Oxygen 8 v2 MIDI 1')

# Return a sine wave of frequency f.
def make_sin(f):
    period = sample_rate / f
    # Need enough cycles to be able to wrap around when
    # generating a block.
    ncycles = math.ceil(blocksize / period)
    nsin = round(ncycles * period)
    t_period = np.linspace(0, ncycles * (2 * np.pi), nsin, dtype=np.float32)
    return 0.8 * np.sin(t_period)

# Precalculate wave tables
notes = []
for note in range(128):
    f = 440 * 2 ** ((note - 69) / 12)
    notes.append(make_sin(f))

# Wave table for silence.
silence_table = np.zeros(blocksize, dtype=np.float32)

class Note:
    def __init__(self, key=None):
        self.t = 0
        if key is None:
            self.wave_table = silence_table
        else:
            self.wave_table = notes[key]
    
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

        # Get the samples.
        self.t += frame_count
        return output
        
# Silence "note".
silence = Note()

# Currently playing note.
current_note = silence

# This callback is called by `sounddevice` to get some
# samples to output. It's the heart of sound generation in
# the synth.
def output_callback(out_data, frame_count, time_info, status):
    # A non-None status indicates that something has
    # happened with sound output that shouldn't have.  This
    # is almost always an underrun due to generating samples
    # too slowly.
    if status:
        print("output callback:", status)

    # Get samples from note.
    output = current_note.play(frame_count)

    # Note that we need the out_data slicing to *replace*
    # the data in the array.
    out_data[:] = output.reshape(frame_count,1)

# Install wave table for note given MIDI key number.
def play_note(key=None):
    global current_note
    current_note = Note(key)

# Start audio playing. Must keep up with output from here on.
output_stream = sounddevice.OutputStream(
    samplerate=sample_rate,
    channels=1,
    blocksize=blocksize,
    callback=output_callback,
)
output_stream.start()

# Last note on processed.
last_played = None

# Block waiting for the controller (keyboard) to send a MIDI
# message, then handle it. Return False if the MIDI message
# wants the instrument (synthesizer) to stop, True otherwise.
def process_midi_event():
    global last_played

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
        last_played = key
        play_note(key=key)
    # Remove a note from the sound. If it is already off,
    # this message will be ignored.
    elif mesg_type == 'note_off':
        key = mesg.note
        velocity = round(mesg.velocity / 127, 2)
        if log_notes:
            print('note off', key, mesg.velocity, velocity)
            print('last played', last_played)
        if key == last_played:
            last_played = None
            play_note()
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
while process_midi_event():
    pass
