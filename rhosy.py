import mido

# MIDI controller is currently hardwired.
controller = mido.open_input('USB Oxygen 8 v2 MIDI 1')

# Print MIDI note events if True.
log_notes = True

# Block waiting for the controller (keyboard) to send a MIDI
# message, then handle it. Return False if the MIDI message
# wants the instrument (synthesizer) to stop, True otherwise.
def process_midi_event():
    # These globals define the interface to sound generation.
    global out_keys, out_osc

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
        # out_keys[key] = Note(key, out_osc)
    # Remove a note from the sound. If it is already off,
    # this message will be ignored.
    elif mesg_type == 'note_off':
        key = mesg.note
        velocity = round(mesg.velocity / 127, 2)
        if log_notes:
            print('note off', key, mesg.velocity, velocity)
        #if key in out_keys:
        #    out_keys[key].release()
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
