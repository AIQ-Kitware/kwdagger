#!/usr/bin/env python3
"""
Simple Torch-based computation to demonstrate GPU-aware SLURM options.
"""
import argparse
import json
from pathlib import Path
import torch


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_fpath', required=True)
    parser.add_argument('--summary_fpath', required=True)
    parser.add_argument('--device', default='auto', choices=['auto', 'cpu', 'cuda'], help='device to run on')
    args = parser.parse_args(argv)

    device = args.device
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

    src = Path(args.input_fpath)
    dst = Path(args.summary_fpath)
    dst.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(src.read_text())
    values = torch.tensor(data.get('values', [0]), dtype=torch.float32, device=device)

    squared = values.pow(2)
    total = squared.sum().item()
    mean = squared.mean().item()

    summary = {
        'device': device,
        'cuda_available': torch.cuda.is_available(),
        'sum_of_squares': total,
        'mean_of_squares': mean,
    }
    dst.write_text(json.dumps(summary, indent=2))
    print(f'Ran torch step on {device}, wrote {dst}')


if __name__ == '__main__':
    main()
