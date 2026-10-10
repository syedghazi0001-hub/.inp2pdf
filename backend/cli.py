#!/usr/bin/env python3
"""InPage → PDF converter — command line.

Usage:
  python cli.py document.inp
  python cli.py file1.inp file2.inp -o outdir/
  python cli.py folder_of_inp_files/ -o pdfs/ --scale 1.2 --letter

Runs fully offline.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from inpage2pdf.converter import convert_file
from inpage2pdf.pdf_builder import RenderOptions


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="inpage2pdf",
        description="Convert InPage (.inp) Urdu documents to PDF with real Nastaliq typography.")
    ap.add_argument("inputs", nargs="+",
                    help=".inp files or folders containing .inp files")
    ap.add_argument("-o", "--outdir", default=None,
                    help="output directory (default: alongside each input)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="text size scale 0.5–2.0 (default 1.0)")
    ap.add_argument("--letter", action="store_true", help="US Letter page (default A4)")
    ap.add_argument("--legal", action="store_true", help="US Legal page")
    ap.add_argument("--no-headings", action="store_true",
                    help="disable heading/bold detection from InPage styles")
    ap.add_argument("--no-fixes", action="store_true",
                    help="disable conversion-artifact fixes")
    ap.add_argument("--numbers", action="store_true", help="add page numbers")
    ap.add_argument("--layout", choices=["auto", "table", "flow"], default="auto",
                    help="layout: auto-detect tables (default), force table, or text flow")
    ap.add_argument("--landscape", action="store_true", help="landscape page orientation")
    ap.add_argument("--no-cover", action="store_true",
                    help="do not add a cover title from the filename")
    args = ap.parse_args()

    page = "Legal" if args.legal else ("Letter" if args.letter else "A4")
    opts = RenderOptions(
        page_size=page,
        scale=max(0.5, min(2.0, args.scale)),
        detect_headings=not args.no_headings,
        page_numbers=args.numbers,
        table_mode=args.layout,
        orientation="L" if args.landscape else "P",
    )

    # expand folders / globs
    paths: list[str] = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            paths += sorted(glob.glob(os.path.join(inp, "*.inp")))
            paths += sorted(glob.glob(os.path.join(inp, "*.INP")))
        else:
            paths.append(inp)

    if args.outdir:
        os.makedirs(args.outdir, exist_ok=True)

    failed = 0
    for p in paths:
        out = None
        if args.outdir:
            out = os.path.join(args.outdir,
                               os.path.splitext(os.path.basename(p))[0] + ".pdf")
        print(f"• {p} …", end=" ", flush=True)
        res = convert_file(p, out, opts,
                           apply_fixes=not args.no_fixes,
                           title_from_filename=not args.no_cover)
        if res.ok:
            s = res.stats
            print(f"OK → {res.pdf_path}  "
                  f"({s['paragraphs']} paragraphs, {s['characters']:,} chars, "
                  f"{len(res.unmapped)} unmapped codes)")
        else:
            failed += 1
            print(f"FAILED — {res.error}")
    if failed:
        print(f"\n{failed} file(s) failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
