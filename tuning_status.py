"""Write and display requested tuning and the WebSDR page's local state."""
import json
import os
import tempfile
from pathlib import Path

DEFAULT_STATUS_PATH = '/run/radio/tuning-status.json'


def write_status(status, path=DEFAULT_STATUS_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.tuning-', delete=False) as staging:
        json.dump(status, staging)
        staging.flush()
        os.fsync(staging.fileno())
    os.replace(staging.name, path)


def format_status(status):
    state = status.get('state')
    if state == 'external':
        return 'External PulseAudio feed\nFrequency: not reported'
    if state not in ('configured', 'requested', 'commanded', 'confirmed', 'page_state'):
        return 'Frequency status unavailable\nProvider readback: unavailable'

    frequency = float(status['frequency_khz']) / 1000
    mode = str(status.get('mode', '')).upper()
    third = None
    if state == 'page_state':
        first = f'Requested: {frequency:.3f} MHz {mode}'
        page_mode = str(status.get('page_mode', '')).upper()
        mapping = 'inverted mapping; page-local state' if status.get('mode_inverted') else 'direct mapping; page-local state'
        second = f'WebSDR page mode: {page_mode} ({mapping})'
        low, high = status['passband_hz']
        third = f'Page passband: {low:g}..{high:g} Hz'
    elif state == 'confirmed':
        first = f'Legacy WebSDR page state (not RF-verified): {frequency:.3f} MHz {mode}'
        low, high = status['passband_hz']
        second = f'Page passband: {low:g}..{high:g} Hz'
    elif state == 'configured':
        first = f'Configured: {frequency:.3f} MHz {mode}'
        second = 'Provider readback: no active session'
    elif state == 'commanded':
        first = f'Requested: {frequency:.3f} MHz {mode}'
        second = 'Kiwi command sent; readback unavailable'
    else:
        first = f'Requested: {frequency:.3f} MHz {mode}'
        second = 'WebSDR page readback: pending'

    receiver = status.get('receiver')
    return '\n'.join(item for item in (first, second, third,
                                       f'Receiver: {receiver}' if receiver else '') if item)


def main():
    import tkinter as tk

    root = tk.Tk()
    root.title('Receiver tuning')
    root.geometry('470x135+12+12')
    root.attributes('-topmost', True)
    root.configure(background='#101820')
    label = tk.Label(root, justify='left', anchor='w', padx=14, pady=10,
                     font=('DejaVu Sans Mono', 11, 'bold'), fg='#e8f0f7',
                     bg='#101820')
    label.pack(fill='both', expand=True)

    def refresh():
        try:
            status = json.loads(Path(os.environ.get('TUNING_STATUS_FILE', DEFAULT_STATUS_PATH)).read_text())
            label.configure(text=format_status(status))
        except (OSError, ValueError, KeyError, TypeError):
            label.configure(text='Frequency status unavailable\nProvider readback: unavailable')
        root.after(800, refresh)

    refresh()
    root.mainloop()


if __name__ == '__main__':
    main()
