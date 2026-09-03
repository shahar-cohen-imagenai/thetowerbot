"""Propose labels for the glyphs `build_atlas.py` dumped, then apply them.

Labelling by hand means opening thirty near-identical 20px PNGs and renaming
each one. This does the boring part: every unlabelled `glyph_*.png` is scored
against an ALREADY COMPLETE size class, and anything that matches a digit
confidently is renamed for you.

Cross-class matching works because `digits.Atlas.match` resizes the candidate
to each template rather than the other way round - the three size classes are
the same font at three sizes, so shape survives the rescale. Measured against
the glyphs already labelled by hand, all nine digits came back correct at
>=0.63 while the letters and the coin icon - which the `wallet` reference has
no template for - all scored <=0.48. That gap is what `--threshold` sits in.

    uv run tools/label_glyphs.py --size-class price            # propose only
    uv run tools/label_glyphs.py --size-class price --apply    # and rename

A glyph that scores below the threshold, or that ties too closely with the
runner-up, is never renamed: it lands in the contact sheet instead, for you to
look at. That is the intended path for every non-digit - `K`, `M`, the coin,
the caption letters - because no reference class contains them yet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import digits  # noqa: E402

# Below this, the best match is not trusted enough to rename a file on.
DEFAULT_THRESHOLD = 0.60
# The best match must also beat the runner-up by this much. A `6` scoring
# 0.74 with `5` at 0.72 is a coin toss, not a reading.
DEFAULT_MARGIN = 0.08


class Proposal(NamedTuple):
    path: Path
    label: str | None
    score: float
    runner_up: str | None
    runner_up_score: float

    @property
    def margin(self) -> float:
        return self.score - self.runner_up_score


def score_glyph(glyph: digits.Image, atlas: digits.Atlas) -> list[tuple[float, str]]:
    """Every reference label scored against `glyph`, best first."""
    scored: list[tuple[float, str]] = []
    for label, template in atlas._glyphs.items():  # noqa: SLF001 - same package
        resized = cv2.resize(
            glyph, (template.shape[1], template.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
        scored.append(
            (float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)[0][0]), label)
        )
    scored.sort(reverse=True)
    return scored


def propose(
    directory: Path, atlas: digits.Atlas, threshold: float, margin: float
) -> list[Proposal]:
    """One Proposal per DISTINCT unlabelled glyph in `directory`.

    A 40-frame harvest re-reads the same four prices every frame, so the raw
    dump is hundreds of files holding maybe twenty shapes. Byte-identical
    crops are collapsed here - not to save work, but because a review sheet
    with three hundred cells is one nobody reads.
    """
    proposals: list[Proposal] = []
    seen: set[tuple[int, int, bytes]] = set()
    for path in sorted(directory.glob("glyph_*.png")):
        glyph = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if glyph is None:
            continue
        fingerprint = (*glyph.shape, glyph.tobytes())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        scored = score_glyph(glyph, atlas)
        best_score, best_label = scored[0]
        second_score, second_label = scored[1] if len(scored) > 1 else (0.0, None)
        confident = best_score >= threshold and best_score - second_score >= margin
        proposals.append(
            Proposal(
                path=path,
                label=best_label if confident else None,
                score=best_score,
                runner_up=second_label,
                runner_up_score=second_score,
            )
        )
    return proposals


def contact_sheet(
    proposals: list[Proposal], out: Path, columns: int = 8, cell: int = 96
) -> None:
    """One image showing every glyph with its proposed label and score.

    Written because verifying thirty proposals means LOOKING at thirty glyphs,
    and thirty separate PNGs is thirty separate openings.
    """
    if not proposals:
        return
    rows = (len(proposals) + columns - 1) // columns
    header = 28
    sheet = np.full(((cell + header) * rows, cell * columns), 40, dtype=np.uint8)

    for index, proposal in enumerate(proposals):
        glyph = cv2.imread(str(proposal.path), cv2.IMREAD_GRAYSCALE)
        if glyph is None:
            continue
        height, width = glyph.shape
        scale = min((cell - 16) / height, (cell - 16) / width)
        resized = cv2.resize(
            glyph, (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_NEAREST,
        )
        row, column = divmod(index, columns)
        top = row * (cell + header) + header
        left = column * cell
        y = top + (cell - resized.shape[0]) // 2
        x = left + (cell - resized.shape[1]) // 2
        sheet[y : y + resized.shape[0], x : x + resized.shape[1]] = resized

        caption = f"{index}:{proposal.label or '?'} {proposal.score:.2f}"
        cv2.putText(
            sheet, caption, (left + 4, top - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255,), 1, cv2.LINE_AA,
        )
        cv2.rectangle(
            sheet, (left, top - header), (left + cell - 1, top + cell - 1), (110,), 1
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet)


def apply(proposals: list[Proposal], directory: Path, replace: bool) -> list[str]:
    """Rename confident proposals to their label. Returns a line per action.

    The best-scoring instance of each label wins; the rest are left alone,
    because a second `7` is not evidence of anything and a directory of
    `7_alt.png` files is just noise to delete later.
    """
    best: dict[str, Proposal] = {}
    for proposal in proposals:
        if proposal.label is None:
            continue
        current = best.get(proposal.label)
        if current is None or proposal.score > current.score:
            best[proposal.label] = proposal

    lines: list[str] = []
    for label, proposal in sorted(best.items()):
        filename = digits.GLYPH_FILENAMES.get(label)
        if filename is None:
            lines.append(f"  {proposal.path.name}: {label!r} has no filename - skipped")
            continue
        target = directory / f"{filename}.png"
        if target.exists() and not replace:
            lines.append(f"  {proposal.path.name}: {target.name} already labelled - kept")
            continue
        proposal.path.rename(target)
        lines.append(f"  {proposal.path.name} -> {target.name}  ({proposal.score:.3f})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size-class", choices=digits.ALL_SIZE_CLASSES, required=True)
    parser.add_argument(
        "--reference", default="wallet",
        help="the complete size class to label against (default: wallet)",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    parser.add_argument("--sheet", type=Path, default=None, help="contact sheet path")
    parser.add_argument(
        "--only-new", action="store_true",
        help="list and sheet only glyphs this class does not already have",
    )
    parser.add_argument("--apply", action="store_true", help="perform the renames")
    parser.add_argument(
        "--replace", action="store_true",
        help="overwrite a label that already has a file (default: keep the old one)",
    )
    args = parser.parse_args(argv)

    if args.reference == args.size_class:
        raise SystemExit("--reference must be a DIFFERENT size class")

    directory = config.ATLAS_DIR / args.size_class
    reference = digits.Atlas(config.ATLAS_DIR / args.reference)
    if not reference.labels:
        raise SystemExit(f"reference class {args.reference!r} has no labelled glyphs")

    proposals = propose(directory, reference, args.threshold, args.margin)
    if not proposals:
        raise SystemExit(
            f"no glyph_*.png in {directory} - run build_atlas.py --size-class "
            f"{args.size_class} first"
        )

    have = {digits.GLYPH_LABELS.get(p.stem) for p in directory.glob("*.png")}
    print(f"{len(proposals)} distinct unlabelled glyphs in {directory}")
    print(f"already labelled: {''.join(sorted(x for x in have if x)) or '(none)'}\n")

    shown = proposals
    if args.only_new:
        shown = [p for p in proposals if p.label is None or p.label not in have]
        print(f"showing {len(shown)} of {len(proposals)} - new labels and unsure only\n")

    for index, proposal in enumerate(shown):
        verdict = proposal.label if proposal.label else "UNSURE - eyeball it"
        print(
            f"  [{index:2}] {proposal.path.name}  {verdict:16}"
            f" score={proposal.score:.3f} runner-up={proposal.runner_up!r}"
            f" {proposal.runner_up_score:.3f}"
        )

    sheet = args.sheet or config.ATLAS_DIR / f"_sheet_{args.size_class}.png"
    if shown:
        contact_sheet(shown, sheet)
        print(f"\ncontact sheet: {sheet}")
    else:
        print("\nnothing new to look at - no contact sheet written")

    if args.apply:
        print("\nrenaming:")
        for line in apply(proposals, directory, args.replace):
            print(line)
    else:
        print("\n(dry run - pass --apply to rename)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
