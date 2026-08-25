#!/usr/bin/env python3
"""Tiny CLIs used by the compile-time gather tutorial."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    train = subparsers.add_parser('train')
    train.add_argument('--data_fpath', required=True)
    train.add_argument('--algorithm', required=True)
    train.add_argument('--seed', required=True, type=int)
    train.add_argument('--fold', required=True, type=int)
    train.add_argument('--checkpoint_fpath', required=True)

    ensemble = subparsers.add_parser('ensemble')
    ensemble.add_argument('--algorithm', required=True)
    ensemble.add_argument('--seed', required=True, type=int)
    ensemble.add_argument('--checkpoints_fpath', required=True)
    ensemble.add_argument('--ensemble_fpath', required=True)

    evaluate = subparsers.add_parser('evaluate')
    evaluate.add_argument('--algorithm', required=True)
    evaluate.add_argument('--seed', required=True, type=int)
    evaluate.add_argument('--test_set', required=True)
    evaluate.add_argument('--ensemble_fpath', required=True)
    evaluate.add_argument('--metrics_fpath', required=True)

    args = parser.parse_args()
    if args.command == 'train':
        checkpoint_fpath = Path(args.checkpoint_fpath)
        checkpoint_fpath.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_fpath.write_text(
            f'algorithm={args.algorithm},seed={args.seed},fold={args.fold}\n'
        )
    elif args.command == 'ensemble':
        member_fpaths = [
            Path(line)
            for line in Path(args.checkpoints_fpath).read_text().splitlines()
        ]
        ensemble_fpath = Path(args.ensemble_fpath)
        ensemble_fpath.parent.mkdir(parents=True, exist_ok=True)
        ensemble_fpath.write_text(
            ''.join(member_fpath.read_text() for member_fpath in member_fpaths)
        )
    elif args.command == 'evaluate':
        members = Path(args.ensemble_fpath).read_text().splitlines()
        metrics_fpath = Path(args.metrics_fpath)
        metrics_fpath.parent.mkdir(parents=True, exist_ok=True)
        metrics_fpath.write_text(
            json.dumps(
                {
                    'algorithm': args.algorithm,
                    'seed': args.seed,
                    'test_set': args.test_set,
                    'num_members': len(members),
                    'members': members,
                },
                indent=2,
            )
        )


if __name__ == '__main__':
    main()
