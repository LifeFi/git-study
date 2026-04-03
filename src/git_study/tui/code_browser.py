from difflib import SequenceMatcher, unified_diff
from functools import lru_cache

from pygments import highlight
from pygments.formatters import Terminal256Formatter
from pygments.lexers import TextLexer, get_lexer_by_name
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Label, Static, Tree

from textual.events import Click

from ..domain.code_context import (
    detect_code_language,
    get_commit_parent_sha,
    get_file_content_at_commit_or_empty,
    get_range_changed_file_paths,
    list_commit_tree_files,
)
from ..domain.repo_context import get_repo


def compute_diff_annotations(
    base_content: str, target_content: str
) -> tuple[set[int], set[int], list[str], list[int]]:
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    matcher = SequenceMatcher(a=base_lines, b=target_lines)

    added_target_lines: set[int] = set()
    replaced_target_lines: set[int] = set()
    removed_chunks: list[str] = []
    removed_base_lines: list[int] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "insert":
            added_target_lines.update(range(j1 + 1, j2 + 1))
        elif tag == "replace":
            replaced_target_lines.update(range(j1 + 1, j2 + 1))

        if tag in {"replace", "delete"} and i1 != i2:
            removed_base_lines.extend(range(i1 + 1, i2 + 1))
            removed_body = "\n".join(base_lines[i1:i2]).strip()
            if removed_body:
                removed_chunks.append(
                    f"base {i1 + 1}-{i2}\n{removed_body}"
                )

    return added_target_lines, replaced_target_lines, removed_chunks, removed_base_lines


def build_current_text(language: str, target_content: str) -> Text:
    highlighted_target_lines = highlight_code_lines(target_content, language)
    rendered = Text(no_wrap=True)
    for line_number, line in enumerate(highlighted_target_lines, start=1):
        rendered.append(f"{line_number:4} ", style="dim")
        rendered.append("  ", style="dim")
        rendered.append_text(line)
        rendered.append("\n")
    if not rendered.plain.strip():
        rendered.append("파일 내용을 표시할 수 없습니다.\n", style="dim")
    return rendered


def format_line_ranges(lines: list[int]) -> str:
    if not lines:
        return "-"
    ranges: list[str] = []
    start = prev = lines[0]
    for line in lines[1:]:
        if line == prev + 1:
            prev = line
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = line
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def build_diff_text_renderable(
    path: str,
    language: str,
    base_content: str,
    target_content: str,
) -> Text:
    added_target_lines, replaced_target_lines, removed_chunks, removed_base_lines = compute_diff_annotations(
        base_content,
        target_content,
    )

    rendered = Text(no_wrap=True)
    rendered.append(" A added ", style="black on green4")
    rendered.append("  ")
    rendered.append(" M changed ", style="black on dark_olive_green3")
    rendered.append("  ")
    rendered.append(" D removed ", style="red")
    rendered.append("\n")
    rendered.append(
        f"added: {format_line_ranges(sorted(added_target_lines))}",
        style="green",
    )
    rendered.append("    ")
    rendered.append(
        f"changed: {format_line_ranges(sorted(replaced_target_lines))}",
        style="yellow",
    )
    rendered.append("    ")
    rendered.append(
        f"removed: {format_line_ranges(sorted(removed_base_lines))}",
        style="red",
    )
    rendered.append("\n\n")
    rendered.append(f"Diff ({language or 'text'})  {path}", style="bold cyan")
    rendered.append("\n\n")
    rendered.append_text(render_current_code_text(language, base_content, target_content))

    if removed_chunks:
        removed_preview = "\n\n".join(removed_chunks[:3])
        if len(removed_chunks) > 3:
            removed_preview += f"\n\n... {len(removed_chunks) - 3} more removed chunks"
        rendered.append("\n\n")
        rendered.append("Removed In Range", style="bold red")
        rendered.append("\n\n")
        rendered.append(removed_preview, style="red")
        rendered.append("\n")

    return rendered


def render_current_code_text(language: str, base_content: str, target_content: str) -> Text:
    base_lines = base_content.splitlines()
    target_lines = target_content.splitlines()
    highlighted_base_lines = highlight_code_lines(base_content, language)
    highlighted_target_lines = highlight_code_lines(target_content, language)
    max_line_width = max(
        [len(line.plain) for line in highlighted_base_lines + highlighted_target_lines] or [0]
    )

    matcher = SequenceMatcher(a=base_lines, b=target_lines)
    rendered = Text()
    old_line_no = 1
    new_line_no = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                rendered.append(f"{new_line_no:4} ", style="dim")
                rendered.append("  ", style="dim")
                rendered.append_text(highlighted_target_lines[j1 + offset])
                rendered.append("\n")
                old_line_no += 1
                new_line_no += 1
        elif tag == "insert":
            for offset in range(j2 - j1):
                rendered.append(f"{new_line_no:4} ", style="dim")
                rendered.append("A ", style="bold green")
                rendered.append_text(
                    tint_line(
                        highlighted_target_lines[j1 + offset],
                        "on color(23)",
                        pad_to=max_line_width,
                    )
                )
                rendered.append("\n")
                new_line_no += 1
        elif tag == "delete":
            for offset in range(i2 - i1):
                rendered.append(f"{old_line_no:4} ", style="dim")
                rendered.append("D ", style="bold red")
                rendered.append_text(
                    tint_line(
                        highlighted_base_lines[i1 + offset],
                        "on dark_red",
                        pad_to=max_line_width,
                    )
                )
                rendered.append("\n")
                old_line_no += 1
        elif tag == "replace":
            for offset in range(i2 - i1):
                rendered.append(f"{old_line_no:4} ", style="dim")
                rendered.append("D ", style="bold red")
                rendered.append_text(
                    tint_line(
                        highlighted_base_lines[i1 + offset],
                        "on dark_red",
                        pad_to=max_line_width,
                    )
                )
                rendered.append("\n")
                old_line_no += 1
            for offset in range(j2 - j1):
                rendered.append(f"{new_line_no:4} ", style="dim")
                rendered.append("M ", style="bold yellow")
                rendered.append_text(
                    tint_line(
                        highlighted_target_lines[j1 + offset],
                        "on color(58)",
                        pad_to=max_line_width,
                    )
                )
                rendered.append("\n")
                new_line_no += 1

    if not rendered.plain.strip():
        rendered.append("파일 내용을 표시할 수 없습니다.\n", style="dim")
    return rendered


@lru_cache(maxsize=32)
def _highlight_code_lines_cached(content: str, language: str) -> tuple[Text, ...]:
    """Pygments 하이라이팅 결과를 LRU 캐시로 보관 (파일 전환 시 재계산 방지)."""
    lexer = TextLexer()
    if language and language != "text":
        try:
            lexer = get_lexer_by_name(language)
        except Exception:
            lexer = TextLexer()
    source = content or "\n"
    ansi = highlight(source, lexer, Terminal256Formatter(style="monokai"))
    highlighted_lines = [Text.from_ansi(line) for line in ansi.splitlines()]
    expected_line_count = max(1, len(source.splitlines()))

    if len(highlighted_lines) < expected_line_count:
        highlighted_lines.extend(Text() for _ in range(expected_line_count - len(highlighted_lines)))
    elif len(highlighted_lines) > expected_line_count:
        highlighted_lines = highlighted_lines[:expected_line_count]

    if not highlighted_lines:
        return (Text(),)
    return tuple(highlighted_lines)


def highlight_code_lines(content: str, language: str) -> list[Text]:
    return list(_highlight_code_lines_cached(content, language))


def tint_line(line: Text, style: str, pad_to: int = 0) -> Text:
    tinted = line.copy()
    tinted.stylize(style)
    pad_length = max(0, pad_to - len(tinted.plain))
    if pad_length:
        tinted.append(" " * pad_length, style=style)
    return tinted


class CodeBrowserDock(Vertical):
    DEFAULT_CSS = """
    CodeBrowserDock.-files-collapsed #code-file-panel {
        width: 3;
        min-width: 3;
        padding: 0;
    }

    CodeBrowserDock.-files-collapsed #code-file-title,
    CodeBrowserDock.-files-collapsed #code-file-tree {
        display: none;
    }

    CodeBrowserDock {
        display: none;
        width: 1fr;
        min-width: 56;
        border: round $accent;
        padding: 0 1;
    }

    #code-browser {
        width: 1fr;
        height: 1fr;
        background: $surface;
        padding: 0;
    }

    #code-browser-title {
        color: $accent;
        text-style: bold;
        margin: 0;
    }

    #code-browser-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #code-browser-header-spacer {
        width: 1fr;
    }

    #code-browser-close {
        width: auto;
        min-width: 5;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
        tint: transparent;
    }

    #code-browser-close:hover,
    #code-browser-close:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #code-browser-body {
        height: 1fr;
        margin-top: 0;
    }

    #code-file-panel,
    #code-view-panel {
        border: round $accent;
        padding: 0 1;
    }

    #code-file-panel {
        width: 36;
        min-width: 28;
        margin-right: 1;
    }

    #code-file-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #code-file-header-spacer {
        width: 1fr;
    }

    #code-file-toggle {
        width: auto;
        min-width: 1;
        height: 1;
        min-height: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold;
    }

    #code-file-toggle:hover,
    #code-file-toggle:focus {
        background: transparent;
        border: none;
        color: cyan;
        text-style: bold underline;
    }

    #code-file-collapsed-indicator {
        display: none;
        width: 1fr;
        align: center middle;
    }

    CodeBrowserDock.-files-collapsed #code-file-collapsed-indicator {
        display: block;
        color: cyan;
        text-style: bold;
    }

    #code-file-tree {
        height: 1fr;
        margin-top: 1;
    }

    #code-view-panel {
        width: 1fr;
    }

    #code-view-title {
        width: auto;
        color: $accent;
        text-style: bold;
        margin: 0;
    }

    #code-view-header {
        height: auto;
        width: 100%;
        align: left middle;
    }

    #code-view-header-spacer {
        width: 1fr;
    }

    #code-view-mode-group {
        width: 18;
        min-width: 18;
        height: auto;
        align: right middle;
        padding: 0;
    }

    .code-view-toggle {
        width: auto;
        min-width: 6;
        height: 1;
        min-height: 1;
        max-height: 1;
        padding: 0 1;
        margin: 0;
        background: transparent;
        border: none;
        outline: none;
        tint: transparent;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #code-view-mode-separator {
        width: auto;
        color: $text-muted;
        padding: 0;
        margin: 0;
    }

    .code-view-toggle:hover,
    .code-view-toggle:focus {
        background: transparent;
        border: none;
        outline: none;
        tint: transparent;
        color: cyan;
        text-style: bold underline;
    }

    .code-view-toggle.active-toggle {
        height: 1;
        min-height: 1;
        max-height: 1;
        background: transparent;
        border: none;
        outline: none;
        tint: transparent;
        color: cyan;
        text-style: bold;
    }

    .code-view-toggle.active-toggle:hover,
    .code-view-toggle.active-toggle:focus {
        background: transparent;
        border: none;
        outline: none;
        tint: transparent;
        color: cyan;
        text-style: bold underline;
    }

    #code-view-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    #code-view-scroll {
        height: 1fr;
        border: round $panel;
        background: $boost;
    }

    #code-view-content {
        width: auto;
        height: auto;
        padding: 1;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.repo_source = "local"
        self.github_repo_url: str | None = None
        self.oldest_commit_sha = ""
        self.newest_commit_sha = ""
        self.target_commit_sha = ""
        self.base_commit_sha: str | None = None
        self.repo = None
        self.changed_paths: set[str] = set()
        self.file_paths: list[str] = []
        self.current_file_path: str | None = None
        self.view_mode = "diff"
        self.files_panel_collapsed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="code-browser"):
            with Horizontal(id="code-browser-header"):
                yield Label("Code Browser", id="code-browser-title")
                yield Static("", id="code-browser-header-spacer")
                yield Button("Close", id="code-browser-close")
            with Horizontal(id="code-browser-body"):
                with Vertical(id="code-file-panel"):
                    with Horizontal(id="code-file-header"):
                        yield Label("Files", classes="section-title", id="code-file-title")
                        yield Static("", id="code-file-header-spacer")
                        yield Button("<", id="code-file-toggle")
                    yield Static(">", id="code-file-collapsed-indicator")
                    yield Tree("Repository", id="code-file-tree")
                with Vertical(id="code-view-panel"):
                    with Horizontal(id="code-view-header"):
                        yield Label("Source View", id="code-view-title")
                        yield Static("", id="code-view-header-spacer")
                        with Horizontal(id="code-view-mode-group"):
                            yield Static(
                                "Diff",
                                id="code-view-mode-diff",
                                classes="code-view-toggle active-toggle",
                            )
                            yield Static("|", id="code-view-mode-separator")
                            yield Static(
                                "Current",
                                id="code-view-mode-current",
                                classes="code-view-toggle",
                            )
                    yield Static("", id="code-view-subtitle")
                    with VerticalScroll(id="code-view-scroll"):
                        yield Static("", id="code-view-content")

    def on_mount(self) -> None:
        tree = self.query_one("#code-file-tree", Tree)
        tree.show_root = False

    def show_range(
        self,
        *,
        repo_source: str,
        github_repo_url: str | None,
        oldest_commit_sha: str,
        newest_commit_sha: str,
        title_suffix: str = "",
    ) -> None:
        self.repo_source = repo_source
        self.github_repo_url = github_repo_url
        self.oldest_commit_sha = oldest_commit_sha
        self.newest_commit_sha = newest_commit_sha
        self.target_commit_sha = newest_commit_sha
        self.repo = get_repo(
            repo_source=self.repo_source,
            github_repo_url=self.github_repo_url,
            refresh_remote=False,
        )
        self.base_commit_sha = get_commit_parent_sha(self.repo, self.oldest_commit_sha)
        base_commit = self.repo.commit(self.base_commit_sha) if self.base_commit_sha else None
        target_commit = self.repo.commit(self.target_commit_sha)
        self.changed_paths = set(
            get_range_changed_file_paths(base_commit, target_commit)
            if base_commit is not None
            else []
        )
        self.file_paths = list_commit_tree_files(self.repo, self.target_commit_sha)
        self.current_file_path = None
        self.display = True
        self.query_one("#code-browser-title", Label).update(
            f"Code Browser  {title_suffix}".rstrip()
        )
        self._populate_tree()

    def hide_panel(self) -> None:
        self.display = False

    def fixed_panel_width(self) -> int:
        return 4 if self.files_panel_collapsed else 40

    def _populate_tree(self) -> None:
        tree = self.query_one("#code-file-tree", Tree)
        tree.reset("Repository")
        tree.show_root = False
        root = tree.root
        for path in self.file_paths:
            self._add_path_to_tree(root, path)
        root.expand()
        first_file = next(iter(self.changed_paths), None) or (
            self.file_paths[0] if self.file_paths else None
        )
        if first_file:
            self._select_path_in_tree(tree, first_file)
            self._show_file(first_file)
        tree.focus()

    def _add_path_to_tree(self, root, path: str) -> None:
        current = root
        parts = path.split("/")
        current_path = []
        for index, part in enumerate(parts):
            current_path.append(part)
            node_path = "/".join(current_path)
            is_leaf = index == len(parts) - 1
            existing = next(
                (child for child in current.children if child.data == node_path),
                None,
            )
            if existing is not None:
                current = existing
                continue
            label = Text(part)
            if self._path_is_changed_or_contains_changed(node_path):
                label.stylize("bold green")
            node_label = part if not self._path_is_changed_or_contains_changed(node_path) else label
            if is_leaf:
                current.add_leaf(node_label, data=node_path)
                return
            current = current.add(node_label, data=node_path)

    def _path_is_changed_or_contains_changed(self, node_path: str) -> bool:
        return any(
            changed_path == node_path or changed_path.startswith(f"{node_path}/")
            for changed_path in self.changed_paths
        )

    def _select_path_in_tree(self, tree: Tree, target_path: str) -> None:
        def walk(node):
            if node.data == target_path:
                return node
            for child in node.children:
                found = walk(child)
                if found is not None:
                    return found
            return None

        node = walk(tree.root)
        if node is not None:
            node.expand()
            tree.select_node(node)

    def _show_file(self, path: str) -> None:
        if self.repo is None:
            return
        self.current_file_path = path
        base_content = get_file_content_at_commit_or_empty(self.repo, self.base_commit_sha, path)
        target_content = get_file_content_at_commit_or_empty(
            self.repo,
            self.target_commit_sha,
            path,
        )
        language = detect_code_language(path) or "text"
        subtitle = f"{path}  |  {language}  |  target {self.target_commit_sha[:7]}"
        if path in self.changed_paths:
            subtitle += "  |  changed"
        else:
            subtitle += "  |  unchanged"
        self.query_one("#code-view-subtitle", Static).update(subtitle)
        content = self.query_one("#code-view-content", Static)
        if self.view_mode == "diff":
            content.update(
                build_diff_text_renderable(path, language, base_content, target_content)
            )
        else:
            content.update(build_current_text(language, target_content))
        self.query_one("#code-view-scroll", VerticalScroll).scroll_home(animate=False)

    def _set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        diff_button = self.query_one("#code-view-mode-diff", Static)
        current_button = self.query_one("#code-view-mode-current", Static)
        if mode == "diff":
            diff_button.add_class("active-toggle")
            current_button.remove_class("active-toggle")
        else:
            current_button.add_class("active-toggle")
            diff_button.remove_class("active-toggle")
        if self.current_file_path:
            self._show_file(self.current_file_path)

    @on(Tree.NodeSelected, "#code-file-tree")
    def handle_file_selected(self, event: Tree.NodeSelected) -> None:
        path = event.node.data
        if not path or path not in self.file_paths:
            return
        self._show_file(path)

    @on(Button.Pressed, "#code-file-toggle")
    def handle_file_panel_toggle(self) -> None:
        self.files_panel_collapsed = not self.files_panel_collapsed
        self.set_class(self.files_panel_collapsed, "-files-collapsed")
        toggle = self.query_one("#code-file-toggle", Button)
        toggle.label = ">" if self.files_panel_collapsed else "<"
        if hasattr(self.app, "_update_workspace_widths"):
            self.app.call_after_refresh(self.app._update_workspace_widths)

    @on(Click, "#code-file-panel")
    def handle_file_panel_click(self, event: Click) -> None:
        if not self.files_panel_collapsed:
            return
        self.handle_file_panel_toggle()
        event.stop()

    @on(Click, "#code-view-mode-diff")
    def handle_view_mode_diff(self) -> None:
        self._set_view_mode("diff")

    @on(Click, "#code-view-mode-current")
    def handle_view_mode_current(self) -> None:
        self._set_view_mode("current")
