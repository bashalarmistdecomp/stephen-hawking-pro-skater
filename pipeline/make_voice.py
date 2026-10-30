"""The Hawking skater's voice lines (plain Python 3, espeak-ng and ffmpeg).

  python3 pipeline/make_voice.py                      # finish.json's lines -> assets/voice/
  python3 pipeline/make_voice.py --all                # make every line again
  python3 pipeline/make_voice.py --say "Any words."   # try a line: build/voice/<words>.wav, played

A line is made like this: espeak-ng says it (its Klatt formant voices, with finish.json's
voice.espeak.args), ffmpeg filters it (voice.filter) to 44.1 kHz mono 16-bit (the rate the game
mixes at, so it plays as is), and its peak is set to voice.peak_db.

The clips are part of the repository (assets/voice/<model>_<line>.wav). Next to them,
voice.json records what each clip was made from. Only lines that are new or changed since are
made again, so espeak-ng is needed only to change them. build_mod.py puts the clips in the mod.
--say makes any text the same way, to hear it before adding it to finish.json.
"""
import argparse
import array
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = 'install espeak-ng and ffmpeg (Arch: sudo pacman -S espeak-ng ffmpeg; ' \
          'Debian/Ubuntu: sudo apt install espeak-ng ffmpeg)'


def log(*m):
    print('[voice]', *m, flush=True)


def normalise(path, peak_db):
    with wave.open(path, 'rb') as w:
        params = w.getparams()
        pcm = array.array('h', w.readframes(params.nframes))
    if sys.byteorder == 'big':
        pcm.byteswap()
    top = max((abs(s) for s in pcm), default=0)
    if top:
        k = 32767 * 10 ** (peak_db / 20) / top
        pcm = array.array('h', (max(-32768, min(32767, int(round(s * k)))) for s in pcm))
    if sys.byteorder == 'big':
        pcm.byteswap()
    with wave.open(path, 'wb') as w:
        w.setparams(params)
        w.writeframes(pcm.tobytes())
    return params.nframes / params.framerate


def speak(espeak, v, text, out):
    """One line, made as finish.json's voice section says; returns its length in seconds."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, 'raw.wav')
        subprocess.run([espeak] + v['espeak']['args'] + ['-w', raw, text], check=True)
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', raw,
                        '-af', v['filter'], '-ac', '1', '-ar', '44100', '-c:a', 'pcm_s16le',
                        '-bitexact', out], check=True)
    return normalise(out, v['peak_db'])


def play(path):
    for cmd in (['pw-play', path], ['ffplay', '-v', 'error', '-nodisp', '-autoexit', path],
                ['aplay', '-q', path]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd)
            return
    log('nothing to play it with (pw-play, ffplay or aplay); it is in', path)


def main():
    ap = argparse.ArgumentParser(description='His voice lines (espeak-ng + ffmpeg).')
    ap.add_argument('--finish', default=os.path.join(ROOT, 'pipeline', 'finish.json'))
    ap.add_argument('--out', help='the folder of clips (default assets/voice); with --say, the .wav')
    ap.add_argument('--all', action='store_true', help='make every line again, changed or not')
    ap.add_argument('--say', metavar='TEXT', help='make any line, to try it (played unless --quiet)')
    ap.add_argument('--quiet', action='store_true', help="with --say: don't play it")
    o = ap.parse_args()
    cfg = json.load(open(o.finish))
    v, model = cfg['voice'], cfg['mod']['model']
    espeak = shutil.which('espeak-ng')
    missing = [t for t in ('espeak-ng', 'ffmpeg') if not shutil.which(t)]

    if o.say is not None:
        if missing:
            raise SystemExit('[voice] --say needs %s: %s' % (' and '.join(missing), INSTALL))
        words = re.findall(r'[a-z0-9]+', o.say.lower().replace("'", ''))
        out = o.out or os.path.join(ROOT, 'build', 'voice', '-'.join(words[:6]) or 'line') + '.wav'
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        log('%s: %.1f s' % (out, speak(espeak, v, o.say, out)))
        if not o.quiet:
            play(out)
        return

    out_dir = o.out or os.path.join(ROOT, 'assets', 'voice')
    record = os.path.join(out_dir, 'voice.json')
    made = json.load(open(record)) if os.path.exists(record) else {}
    todo = []
    for name, text in v['lines'].items():
        f = '%s_%s.wav' % (model, name)
        how = {'text': text, 'espeak': v['espeak']['args'], 'filter': v['filter'],
               'peak_db': v['peak_db']}
        if o.all or made.get(f) != how or not os.path.isfile(os.path.join(out_dir, f)):
            todo.append((f, how))
    if not todo:
        log('%d lines, all made' % len(v['lines']))
        return
    if missing:
        # not an error: the mod is built with the clips there are
        log('not made, as there is no %s: %s. To make them, %s'
            % (' or '.join(missing), ', '.join(f for f, _ in todo), INSTALL))
        return
    os.makedirs(out_dir, exist_ok=True)
    for f, how in todo:
        secs = speak(espeak, v, how['text'], os.path.join(out_dir, f))
        made[f] = how
        log('%s: %.1f s  "%s"' % (f, secs, how['text']))
    with open(record, 'w') as w:
        json.dump(made, w, indent=1)
        w.write('\n')


main()
