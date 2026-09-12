from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from py_code_act.domain.execution import DisplayOutput, ErrorOutput, StreamOutput, ValueOutput
from py_code_act.domain.messages import ExecutionMessage, KernelNoticeMessage, ProtocolErrorMessage


def format_execution_result(message: ExecutionMessage) -> str:
    lines = [
        (
            f"<exec_result id={quoteattr(message.exec_id)} status={quoteattr(message.status)} "
            f"kernel_generation={quoteattr(str(message.kernel_generation))}>"
        )
    ]
    for output in message.outputs:
        if isinstance(output, StreamOutput):
            lines.append(
                f"<stream channel={quoteattr(output.channel)}>{escape(output.text)}</stream>"
            )
        elif isinstance(output, ValueOutput):
            lines.append(
                f"<value mime_type={quoteattr(output.mime_type)}>{escape(output.data)}</value>"
            )
        elif isinstance(output, DisplayOutput):
            lines.append(
                f"<display mime_type={quoteattr(output.mime_type)}>{escape(output.data)}</display>"
            )
        elif isinstance(output, ErrorOutput):
            traceback = "\n".join(output.traceback)
            lines.append(
                f"<error name={quoteattr(output.name)} message={quoteattr(output.message)}>"
                f"{escape(traceback)}</error>"
            )
    lines.append("</exec_result>")
    return "\n".join(lines)


def format_protocol_error(message: ProtocolErrorMessage) -> str:
    return (
        f"<protocol_error error={quoteattr(message.error)} "
        f"assistant_message_id={quoteattr(message.assistant_message_id)}>\n"
        f"{escape(message.message)}\n</protocol_error>"
    )


def format_kernel_notice(message: KernelNoticeMessage) -> str:
    return (
        f"<kernel_notice reason={quoteattr(message.reason)} "
        f"kernel_generation={quoteattr(str(message.kernel_generation))}>\n"
        f"{escape(message.message)}\n</kernel_notice>"
    )
