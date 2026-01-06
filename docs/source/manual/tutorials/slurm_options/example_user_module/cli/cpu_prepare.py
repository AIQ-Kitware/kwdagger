#!/usr/bin/env python3
"""
Minimal CPU preprocessing step for the SLURM tutorial.
"""
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--src_fpath', required=True)
    parser.add_argument('--prepared_fpath', required=True)
    parser.add_argument('--uppercase', action='store_true', help='uppercase the text field')
    args = parser.parse_args(argv)

    src = Path(args.src_fpath)
    dst = Path(args.prepared_fpath)
    dst.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(src.read_text())
    text = data.get('text', '')
    values = data.get('values', [])

    if args.uppercase:
        text = text.upper()

    prepared = {
        'text': text,
        'values': values,
        'length': len(text),
        'sum': sum(values),
    }
    dst.write_text(json.dumps(prepared, indent=2))
    print(f'Wrote prepared data to {dst}')


if __name__ == '__main__':
    main()
