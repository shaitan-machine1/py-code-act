from py_code_act.domain.execution import ErrorOutput, StreamOutput, ValueOutput
from py_code_act.domain.messages import ExecutionMessage
from py_code_act.protocol.exec_parser import ExecParser
from py_code_act.protocol.exec_result import format_execution_result


def _parse_chunks(text: str, cuts: tuple[int, ...]):
    parser = ExecParser()
    start = 0
    for cut in cuts:
        parser.feed(text[start:cut])
        start = cut
    parser.feed(text[start:])
    return parser.finalize()


def test_every_single_split_parses_prefix_exec_and_suffix() -> None:
    text = "before\r\n<exec>\r\nprint('x')\r\n</exec>\r\nafter"
    for cut in range(len(text) + 1):
        result = _parse_chunks(text, (cut,))
        assert result.error is None
        assert result.exec_block is not None
        assert result.exec_block.code == "print('x')\r\n"
        assert result.blocks[0].text == "before\r\n"  # type: ignore[union-attr]
        assert result.blocks[-1].text == "after"  # type: ignore[union-attr]


def test_marker_text_inside_python_string_is_not_a_close() -> None:
    result = _parse_chunks('<exec>\nprint("</exec>")\n</exec>', ())
    assert result.error is None
    assert result.exec_block is not None
    assert result.exec_block.code == 'print("</exec>")\n'


def test_malformed_and_multiple_blocks_fail() -> None:
    cases = (
        "</exec>\n",
        "<exec>\n1\n<exec>\n",
        "<exec>\n1\n</exec>\n<exec>\n2\n</exec>",
        "<exec>\n1",
    )
    for text in cases:
        assert _parse_chunks(text, ()).error is not None


def test_plain_text_has_no_execution() -> None:
    result = _parse_chunks("ordinary response", ())
    assert result.error is None
    assert result.exec_block is None
    assert result.blocks[0].text == "ordinary response"  # type: ignore[union-attr]


def test_execution_result_is_ordered_and_xml_escaped() -> None:
    message = ExecutionMessage(
        exec_id='exec_"<&',
        kernel_generation=1,
        status="error",
        outputs=(
            StreamOutput("stdout", "<exec>\n&"),
            ValueOutput("text/plain", "two"),
            ErrorOutput("Bad<Error", 'a "problem"', ("trace >",)),
        ),
        started_at="start",
        finished_at="finish",
    )
    formatted = format_execution_result(message)
    assert "&lt;exec&gt;" in formatted
    assert formatted.index("<stream") < formatted.index("<value") < formatted.index("<error")
    assert "id='exec_\"&lt;&amp;'" in formatted
    assert "trace &gt;" in formatted
