#!/usr/bin/env python3
"""Export a static, explicitly simulated product preview; never copy state."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ('index.html', 'install.html', 'app.js', 'install.js', 'ui.js', 'style.css', 'welcome.html', 'welcome.js')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'site')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or output in (ROOT / 'web', ROOT / 'windows'):
        raise SystemExit('Output must be a dedicated preview directory')
    output.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        data = (ROOT / 'web' / name).read_text()
        if name.endswith('.html'):
            if 'data-mode="live"' not in data:
                raise SystemExit('Missing explicit live/demo switch: ' + name)
            data = data.replace('data-mode="live"', 'data-mode="demo" data-static="true"')
        (output / name).write_text(data)
    (output / '.nojekyll').write_text('')
    print('Static demonstration exported: ' + str(output))


if __name__ == '__main__':
    main()
