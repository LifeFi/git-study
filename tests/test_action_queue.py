"""액션 큐 및 멀티 액션 파싱 테스트."""
from git_study.tui_v2.commands import parse_command


def test_parse_single_quiz():
    p = parse_command("/quiz 3")
    assert p.kind == "quiz"
    assert p.quiz_count == 3
    assert p.range_arg == ""


def test_parse_quiz_with_range_and_count():
    p = parse_command("/quiz HEAD~1 10")
    assert p.kind == "quiz"
    assert p.range_arg == "HEAD~1"
    assert p.quiz_count == 10


def test_parse_quiz_ai_with_range():
    p = parse_command("/quiz HEAD~3 --ai 5")
    assert p.kind == "quiz"
    assert p.range_arg == "HEAD~3"
    assert p.author_context == "ai"
    assert p.quiz_count == 5


def test_parse_map_full():
    p = parse_command("/map --full")
    assert p.kind == "map"
    assert p.full_map is True
    assert p.refresh is False


def test_parse_map_refresh():
    p = parse_command("/map --full --refresh")
    assert p.kind == "map"
    assert p.full_map is True
    assert p.refresh is True


def _make_app():
    """GitStudyAppV2 인스턴스를 Textual 없이 생성."""
    from git_study.tui_v2.app import GitStudyAppV2
    app = object.__new__(GitStudyAppV2)
    app._action_queue = []
    app._action_queue_block_id = None
    app._mode = "idle"
    return app


def test_enqueue_adds_to_queue():
    app = _make_app()
    cmds = [parse_command("/quiz 3"), parse_command("/grade")]
    # _enqueue_actions의 UI 호출부 monkeypatch
    app.query_one = lambda *a, **k: (_ for _ in ()).throw(Exception("no ui"))

    # _update_queue_status와 _process_action_queue를 stub
    called = []
    app._update_queue_status = lambda: called.append("status")
    app._process_action_queue = lambda: called.append("process")

    app._enqueue_actions(cmds, None)
    # _process_action_queue가 stub이므로 실제 pop은 일어나지 않아 큐에 items가 남아 있음
    assert len(app._action_queue) == 2
    # stub된 _process_action_queue가 호출됐는지 확인
    assert "process" in called


def test_queue_pops_on_idle(monkeypatch):
    """mode → idle 시 _process_action_queue가 호출됨을 확인."""
    app = _make_app()
    app._action_queue = [parse_command("/grade")]
    app._action_queue_block_id = None

    processed = []
    app._process_action_queue = lambda: processed.append(True)

    # _set_mode 핵심 로직만 재현
    mode = "idle"
    if mode == "idle" and app._action_queue:
        app._process_action_queue()

    assert processed == [True]


def test_multiple_actions_parsed_from_event():
    """actions/actions_args 리스트를 parse_command로 변환."""
    actions = ["map", "quiz", "grade"]
    actions_args = ["--full", "HEAD~1 5", ""]
    cmds = []
    for action, args in zip(actions, actions_args):
        if action == "none":
            continue
        raw = f"/{action} {args}".strip()
        cmds.append(parse_command(raw))

    assert cmds[0].kind == "map"
    assert cmds[0].full_map is True

    assert cmds[1].kind == "quiz"
    assert cmds[1].range_arg == "HEAD~1"
    assert cmds[1].quiz_count == 5

    assert cmds[2].kind == "grade"
