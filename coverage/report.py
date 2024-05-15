# Licensed under the Apache License: http://www.apache.org/licenses/LICENSE-2.0
# For details: https://github.com/nedbat/coveragepy/blob/master/NOTICE.txt

"""Summary reporting"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import IO, TYPE_CHECKING, Any, Iterable, List, Optional, Tuple

from coverage.diff import unchanged_blocks
from coverage.exceptions import ConfigError, NoDataError
from coverage.misc import human_sorted_items
from coverage.plugin import FileReporter
from coverage.report_core import get_analysis_to_report
from coverage.results import Analysis, Numbers
from coverage.types import TMorf

if TYPE_CHECKING:
    from coverage import Coverage


def format_range(r: dict[str, Any]) -> str:
    """Return a nice string representation of a pair of numbers."""
    if r["start"] == r["end"] - 1:
        s = str(r["start"] + 1)
    else:
        s = f"{r['start'] + 1}-{r['end']}"
    if "same_cov" in r and not r["same_cov"]:
        s = f"**{s}**"
    return s


class SummaryReporter:
    """A reporter for writing the summary report."""

    def __init__(self, coverage: Coverage) -> None:
        self.coverage = coverage
        self.config = self.coverage.config
        self.branches = coverage.get_data().has_arcs()
        self.outfile: Optional[IO[str]] = None
        self.output_format = self.config.format or "text"
        if self.output_format not in {"text", "markdown", "total", "diff"}:
            raise ConfigError(f"Unknown report format choice: {self.output_format!r}")
        self.fr_analysis: List[Tuple[FileReporter, Analysis]] = []
        self.skipped_count = 0
        self.empty_count = 0
        self.total = Numbers(precision=self.config.precision)

    def write(self, line: str) -> None:
        """Write a line to the output, adding a newline."""
        assert self.outfile is not None
        self.outfile.write(line.rstrip())
        self.outfile.write("\n")

    def write_items(self, items: Iterable[str]) -> None:
        """Write a list of strings, joined together."""
        self.write("".join(items))

    def _report_text(
        self,
        header: List[str],
        lines_values: List[List[Any]],
        total_line: List[Any],
        end_lines: List[str],
    ) -> None:
        """Internal method that prints report data in text format.

        `header` is a list with captions.
        `lines_values` is list of lists of sortable values.
        `total_line` is a list with values of the total line.
        `end_lines` is a list of ending lines with information about skipped files.

        """
        # Prepare the formatting strings, header, and column sorting.
        max_name = max([len(line[0]) for line in lines_values] + [5]) + 1
        max_n = max(len(total_line[header.index("Cover")]) + 2, len(" Cover")) + 1
        max_n = max([max_n] + [len(line[header.index("Cover")]) + 2 for line in lines_values])
        formats = {
            "Name": "{:{name_len}}",
            "Stmts": "{:>7}",
            "Miss": "{:>7}",
            "Branch": "{:>7}",
            "BrPart": "{:>7}",
            "Cover": "{:>{n}}",
            "Missing": "{:>10}",
            "∆ Miss": "{:>11}",
        }
        header_items = [
            formats[item].format(item, name_len=max_name, n=max_n)
            for item in header
        ]
        header_str = "".join(header_items)
        rule = "-" * len(header_str)

        # Write the header
        self.write(header_str)
        self.write(rule)

        formats.update(dict(Cover="{:>{n}}%"), Missing="   {:9}")
        for values in lines_values:
            # build string with line values
            line_items = [
                formats[item].format(
                    str(value)
                    if item != "Missing"
                    else ", ".join(format_range(r) for r in value),
                    name_len=max_name, n=max_n - 1) for item, value in
                zip(header, values)

            ]
            self.write_items(line_items)

        # Write a TOTAL line
        if lines_values:
            self.write(rule)

        line_items = [
            formats[item].format(str(value),
            name_len=max_name, n=max_n-1) for item, value in zip(header, total_line)
        ]
        self.write_items(line_items)

        for end_line in end_lines:
            self.write(end_line)

    def _report_markdown(
        self,
        header: List[str],
        lines_values: List[List[Any]],
        total_line: List[Any],
        end_lines: List[str],
    ) -> None:
        """Internal method that prints report data in markdown format.

        `header` is a list with captions.
        `lines_values` is a sorted list of lists containing coverage information.
        `total_line` is a list with values of the total line.
        `end_lines` is a list of ending lines with information about skipped files.

        """
        # Prepare the formatting strings, header, and column sorting.
        max_name = max((len(line[0].replace("_", "\\_")) for line in lines_values), default=0)
        max_name = max(max_name, len("**TOTAL**")) + 1
        formats = {
            "Name": "| {:{name_len}}|",
            "Stmts": "{:>9} |",
            "Miss": "{:>9} |",
            "Branch": "{:>9} |",
            "BrPart": "{:>9} |",
            "Cover": "{:>{n}} |",
            "Missing": "{:>10} |",
            "∆ Miss": "{:>11} |",
        }
        max_n = max(len(total_line[header.index("Cover")]) + 6, len(" Cover "))
        header_items = [
            formats[item].format(
                item.replace(" ", "&nbsp;"),
                name_len=max_name, n=max_n
            ) for item in header
        ]
        header_str = "".join(header_items)
        rule_str = "|" + " ".join(["- |".rjust(len(header_items[0])-1, "-")] +
            ["-: |".rjust(len(item)-1, "-") for item in header_items[1:]]
        )

        # Write the header
        self.write(header_str)
        self.write(rule_str)

        for values in lines_values:
            # build string with line values
            formats.update(dict(Cover="{:>{n}}% |"))
            line_items = [
                formats[item].format(
                    str(value).replace("_", "\\_"), name_len=max_name, n=max_n - 1
                )
                if item != "Missing"
                else ", ".join(format_range(r) for r in value)
                for item, value in zip(header, values)
            ]
            self.write_items(line_items)

        # Write the TOTAL line
        formats.update(dict(Name="|{:>{name_len}} |", Cover="{:>{n}} |"))
        total_line_items: List[str] = []
        for item, value in zip(header, total_line):
            if value == "":
                insert = value
            elif item == "Cover":
                insert = f" **{value}%**"
            else:
                insert = f" **{value}**"
            total_line_items += formats[item].format(insert, name_len=max_name, n=max_n)
        self.write_items(total_line_items)
        for end_line in end_lines:
            self.write(end_line)

    def _report_diff(
        self,
        header: list[str],
        lines_values: list[list[Any]],
        total_line: Optional[list[Any]],
        end_lines: list[str],
        short: bool = False,
    ) -> None:
        """Internal method that prints report data in markdown format.

        `header` is a list with captions.
        `lines_values` is a sorted list of lists containing coverage information.
        `total_line` is a list with values of the total line.
        `end_lines` is a list of ending lines with information about skipped files.

        """
        header_items = [
            (f"<th align={'left' if item == 'Name' else 'right'}>"
             f"{item.replace(' ', '&nbsp;')}</th>")
            for item in header
            if item != "Missing"
        ]
        header_str = "<tr>{}</tr>".format("".join(header_items))

        lines = []
        collapsed_lines = []

        for values in lines_values:
            fr: FileReporter = values[-1]
            # build string with line values
            fields = dict(zip(header, values))
            filename = fields["Name"]
            source = fr.source().splitlines()

            collapse = fields.get("∆ Miss", 0) <= 0
            if "Missing" in fields:
                collapse = all(m.get("same_cov") for m in fields["Missing"])
                changed_file = filename in unchanged_blocks(self.config.base_revision)

                snippets = []
                for m in fields["Missing"]:
                    start = m["start"]
                    end = m["end"]
                    nice_range = f"{start}-{end}" if start != end - 1 else str(start)

                    # Snippet header
                    diff_id = hashlib.sha256(filename.encode()).hexdigest()
                    many = 's' if '-' in nice_range else ''
                    if "same_cov" not in m:
                        loc = f"Missing coverage at line{many} {nice_range}"
                    elif m["same_cov"]:
                        loc = f"Was already missing at line{many} {nice_range}"
                    else:
                        loc = f"New missing coverage at line{many} {nice_range} !"
                    if "GITHUB_PR_NUMBER" in os.environ and changed_file:
                        link = os.environ.get(
                            "GITHUB_PR_NUMBER", "."
                        ) + "/files#diff-{}R{}-R{}".format(diff_id, start, end)
                        loc = f'<a href="{link}">' + loc + "</a>"
                    snippet = loc

                    # Snippet body
                    if not short:
                        snippet_lines = []
                        for i in range(
                            max(0, start - 1),
                            min(end + 1, len(source)),
                        ):
                            is_missing = start <= i < end
                            snippet_line = ("- " if is_missing else " ") + source[i]
                            if not snippet_line.strip():
                                snippet_line = "<span/>"
                            snippet_lines.append(snippet_line)

                        snippet_body = "\n".join(snippet_lines)
                        limit = 256 if collapse else 512 if m.get("same_cov") else 1024

                        if len(snippet_body) > limit:
                            snippet_body = "\n".join([
                                s[:128] + "..." if len(s) > 128 else s
                                for s in
                                (
                                    *snippet_lines[:limit // 128],
                                    "  ...",
                                    *snippet_lines[-limit // 128:],
                                )
                            ])

                        snippet += f'<pre lang="diff">{snippet_body}</pre>'

                    snippets.append(snippet)

                if short:
                    snippets = ["<li>" + s + "</li>" for s in snippets]
                body = "".join(snippets)
                fields["Name"] = (
                    f"<details>"
                    f"<summary>{fields['Name']}</summary>"
                    f"<p>{body}</p>"
                    f"</details>"
                )

            line_items = [
                (
                    f"<td align={'left' if key == 'Name' else 'right'}>"
                    f"{value}"
                    f"{'%' if key == 'Cover' else ''}"
                    f"</td>"
                )
                for key, value in fields.items()
                if key != "Missing"
            ]
            line = f"<tr>{''.join(line_items)}</tr>"

            if not collapse:
                lines.append(line)
            else:
                collapsed_lines.append(line)

        # Write the TOTAL line
        if total_line:
            total_line_items: str = ""
            values = dict(zip(header, total_line))
            for item, value in values.items():
                if item == "Missing":
                    continue
                if value == "":
                    insert = value
                elif item == "Cover":
                    insert = f"<b>{value}%</b>"
                else:
                    insert = f"<b>{value}</b>"
                side = "left" if item == "Name" else "right"
                total_line_items += f"<td align={side}>{insert}</td>"
            lines.append(f"<tr>{total_line_items}</tr>")
        result = (
            f"<table>"
            f"<thead>{header_str}</thead>"
            f"<tbody>{''.join(lines)}</tbody>"
            f"</table>"
        )

        if collapsed_lines:
            result += (
                "\n\n<details><summary>"
                "Files without new missing coverage"
                "</summary>\n"
                f"<table>"
                f"<thead>{header_str}</thead>"
                f"<tbody>{''.join(collapsed_lines)}</tbody>"
                f"</table>"
                "</details>"
            )

        if not short and len(result) > 65536 - 1024:
            self._report_diff(
                header,
                lines_values,
                total_line,
                end_lines,
                short=True)
            self.write("\n\n*Snippets were omitted because the report was too large*")
            return
        else:
            self.write(result)

        if end_lines:
            self.write("")
        for end_line in end_lines:
            self.write(end_line)

        self.write("")

    def report(
        self, morfs: Optional[Iterable[TMorf]], outfile: Optional[IO[str]] = None
    ) -> float:
        """Writes a report summarizing coverage statistics per module.

        `outfile` is a text-mode file object to write the summary to.

        """
        self.outfile = outfile or sys.stdout

        data = self.coverage.get_data()
        data.set_query_contexts(self.config.report_contexts)
        data.load_base_report(self.config.base_coverage_report)
        for fr, analysis in get_analysis_to_report(self.coverage, morfs):
            self.report_one_file(fr, analysis)

        if not self.total.n_files and not self.skipped_count:
            raise NoDataError("No data to report.")

        if self.output_format == "total":
            self.write(self.total.pc_covered_str)
        else:
            self.tabular_report()

        return self.total.pc_covered

    def tabular_report(self) -> None:
        """Writes tabular report formats."""
        # Prepare the header line and column sorting.
        header = ["Name", "Stmts", "Miss"]
        if self.branches:
            header += ["Branch", "BrPart"]
        if self.config.base_coverage_report:
            header += ["∆ Miss"]
        header += ["Cover"]
        if self.config.show_missing:
            header += ["Missing"]

        column_order = {
            "name": header.index("Name") if "Name" in header else None,
            "stmts": header.index("Stmts") if "Stmts" in header else None,
            "miss": header.index("Miss") if "Miss" in header else None,
            "cover": header.index("Cover") if "Cover" in header else None,
            "diff": header.index("∆ Miss") if "∆ Miss" in header else None,
            "branch": header.index("Branch") if "Branch" in header else None,
            "brpart": header.index("BrPart") if "BrPart" in header else None,
        }
        # filter if exist
        column_order = {k: v for k, v in column_order.items() if v is not None}

        # `lines_values` is list of lists of sortable values.
        lines_values = []

        for (fr, analysis) in self.fr_analysis:
            nums = analysis.numbers

            args = [fr.relative_filename(), nums.n_statements, nums.n_missing]
            if self.branches:
                args += [nums.n_branches, nums.n_partial_branches]
            if self.config.base_coverage_report:
                args += [nums.n_diff_missing]
            args += [nums.pc_covered_str]
            if self.config.show_missing:
                args += [analysis.missing_ranges()]
            args += [nums.pc_covered]
            args.append(fr)
            lines_values.append(args)

        # Line sorting.
        sort_option = (self.config.sort or "name").lower()
        reverse = False
        if sort_option[0] == "-":
            reverse = True
            sort_option = sort_option[1:]
        elif sort_option[0] == "+":
            sort_option = sort_option[1:]
        sort_idx = column_order.get(sort_option)
        if sort_idx is None:
            raise ConfigError(f"Invalid sorting option: {self.config.sort!r}")
        if sort_option == "name":
            lines_values = human_sorted_items(lines_values, reverse=reverse)
        else:
            lines_values.sort(
                key=lambda line: (line[sort_idx], line[0]),     # type: ignore[index]
                reverse=reverse,
            )

        # Calculate total if we had at least one file.
        total_line = ["TOTAL", self.total.n_statements, self.total.n_missing]
        if self.branches:
            total_line += [self.total.n_branches, self.total.n_partial_branches]
        if self.config.base_coverage_report:
            total_line += [self.total.n_diff_missing]
        total_line += [self.total.pc_covered_str]
        if self.config.show_missing:
            total_line += [""]

        # Create other final lines.
        end_lines = []
        if self.config.skip_covered and self.skipped_count:
            file_suffix = "s" if self.skipped_count>1 else ""
            end_lines.append(
                f"\n{self.skipped_count} file{file_suffix} skipped due to complete coverage."
            )
        if self.config.skip_empty and self.empty_count:
            file_suffix = "s" if self.empty_count > 1 else ""
            end_lines.append(f"\n{self.empty_count} empty file{file_suffix} skipped.")

        if self.output_format == "markdown":
            formatter = self._report_markdown
        elif self.output_format == "diff":
            formatter = self._report_diff
        else:
            formatter = self._report_text
        formatter(header, lines_values, total_line, end_lines)

    def report_one_file(self, fr: FileReporter, analysis: Analysis) -> None:
        """Report on just one file, the callback from report()."""
        nums = analysis.numbers
        self.total += nums

        no_missing_lines = (nums.n_missing == 0)
        no_missing_branches = (nums.n_partial_branches == 0)
        if self.config.skip_covered and no_missing_lines and no_missing_branches:
            # Don't report on 100% files.
            self.skipped_count += 1
        elif self.config.skip_empty and nums.n_statements == 0:
            # Don't report on empty files.
            self.empty_count += 1
        else:
            self.fr_analysis.append((fr, analysis))
