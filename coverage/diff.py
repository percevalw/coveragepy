import functools
import pathlib
import subprocess
from typing import Dict, List, Tuple


def parse_range_info(range_info):
    if "0,0" == range_info:
        size, start = 1, 1
    elif "," in range_info:
        size, start = map(int, range_info.split(","))
        size = abs(size)
    else:
        size = 1
        start = int(range_info)
    return start - 1, size


@functools.lru_cache(maxsize=None)
def unchanged_blocks(base_branch) -> Dict[str, List[Tuple[int, int, int]]]:
    """
    Returns
    -------
    Dict[str, List[Tuple[int, int, int]]]
        File -> (base_starts, curr_starts, sizes)
        unzipped lists that describe blocks that are unchanged between the two branches
    """

    # Get the diff output for all files between the curr working tree and the base branch
    diff_output = subprocess.check_output(
        ["git", "diff", "--unified=0", base_branch]
    ).decode("utf-8")

    file_blocks = {}
    curr_file = None
    changed_ranges = []

    def compute_unchanged_blocks():
        blocks = []
        if curr_file:
            # Process the previous file
            base_offset = 0
            curr_offset = 0
            for base_start, base_size, curr_start, curr_size in changed_ranges:
                size = base_start - base_offset
                if size > 0:
                    blocks.append(
                        (
                            base_offset,
                            curr_offset,
                            size,
                        )
                    )
                base_offset = base_start + base_size
                curr_offset = curr_start + curr_size

        base_file_content = []
        curr_file_content = []
        try:
            base_file_content = (
                subprocess.check_output(
                    ["git", "show", f"{base_branch}:{curr_file}"],
                    stderr=subprocess.DEVNULL,
                )
                .decode("utf-8")
                .splitlines()
            )
        except subprocess.CalledProcessError:
            pass
        if curr_file:
            try:
                curr_file_content = pathlib.Path(curr_file).read_text().splitlines()
            except FileNotFoundError:
                pass
        blocks.append((len(base_file_content), len(curr_file_content), 0))
        file_blocks[curr_file] = zip(*blocks) if blocks else ([], [], [])

    for line in diff_output.splitlines():
        if line.startswith("diff --git"):
            # Set the curr file to the new one
            compute_unchanged_blocks()
            curr_file = line.split()[-1].split("/", 1)[1]
            changed_ranges = []
        elif line.startswith("@@"):
            # Examples:
            # @@ -2,0 +3,59 @@
            # @@ -85 +85 @@ In EDS-NLP, ...
            range_diff_info = line.split("@@")[1].strip()
            base, curr = range_diff_info.split(" ")
            changed_ranges.append((*parse_range_info(base), *parse_range_info(curr)))
        elif line.startswith("-"):
            continue
        elif line.startswith("+"):
            continue

    # Process the last file
    if curr_file:
        compute_unchanged_blocks()

    return file_blocks
