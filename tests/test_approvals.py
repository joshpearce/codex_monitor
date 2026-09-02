import pytest

from codex_goal_monitor.approvals import AggressiveApprovalHandler


@pytest.mark.asyncio
async def test_approves_managed_command_for_session():
    handler = AggressiveApprovalHandler({"managed"}, "yes")
    result = await handler({
        "id": 4,
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "managed"},
    })
    assert result == {"decision": "acceptForSession"}


@pytest.mark.asyncio
async def test_refuses_unmanaged_thread():
    handler = AggressiveApprovalHandler({"managed"}, "yes")
    with pytest.raises(ValueError, match="unmanaged"):
        await handler({
            "id": 4,
            "method": "item/fileChange/requestApproval",
            "params": {"threadId": "other"},
        })


@pytest.mark.asyncio
async def test_answers_first_user_input_option():
    handler = AggressiveApprovalHandler({"managed"}, "yes")
    result = await handler({
        "id": 4,
        "method": "item/tool/requestUserInput",
        "params": {
            "threadId": "managed",
            "questions": [{"id": "send", "options": [{"label": "Approve"}]}],
        },
    })
    assert result == {"answers": {"send": {"answers": ["Approve"]}}}
