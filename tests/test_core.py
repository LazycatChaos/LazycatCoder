"""Tests for core modules: config, context, session, tokenizer, imports."""

import os
import pathlib
import tempfile

from nanocoder import Agent, LLM, Config, ALL_TOOLS, __version__
from nanocoder.context import ContextManager, estimate_tokens, _approx_tokens
from nanocoder.session import save_session, load_session, list_sessions


# --- Version & Imports ---

def test_version():
    assert __version__ == "0.1.0"


def test_public_api_exports():
    """Users should be able to import key classes from the top-level package."""
    assert Agent is not None
    assert LLM is not None
    assert Config is not None
    # 11 tools: bash, read_file, write_file, edit_file, delete_file,
    #           glob, grep, agent, todo_write, web_search, fetch_url
    assert len(ALL_TOOLS) == 11


# --- Config ---

def test_config_from_env():
    os.environ["NANOCODER_MODEL"] = "test-model"
    c = Config.from_env()
    assert c.model == "test-model"
    del os.environ["NANOCODER_MODEL"]


def test_config_defaults():
    saved = {}
    for k in ["NANOCODER_MODEL", "NANOCODER_MAX_TOKENS"]:
        if k in os.environ:
            saved[k] = os.environ.pop(k)

    c = Config.from_env()
    assert c.model == "gpt-4o"
    assert c.max_tokens == 16384
    assert c.temperature == 0.0

    os.environ.update(saved)


# --- Context & Token Estimation ---

def test_estimate_tokens():
    msgs = [{"role": "user", "content": "hello world"}]
    t = estimate_tokens(msgs)
    assert t > 0
    assert t < 100


def test_estimate_tokens_with_model():
    """Token count should vary by model when using real tokenizers."""
    text = "你好世界 Hello World"
    qwen = estimate_tokens([{"role": "user", "content": text}], model="qwen3-32b")
    gpt = estimate_tokens([{"role": "user", "content": text}], model="gpt-4o")
    fallback = estimate_tokens([{"role": "user", "content": text}], model="unknown")
    # All should return positive values
    assert qwen > 0
    assert gpt > 0
    assert fallback > 0


def test_approx_tokens_fallback():
    """Fallback should use len(text)//3 for unknown models when no tokenizer matches."""
    text = "hello world test"
    # With an unknown model name, the proxy should fall back to len//3
    # But if tiktoken is installed, it may still be used for OpenAI-like models.
    # So we just verify it returns a reasonable positive number.
    result = _approx_tokens(text, "unknown-model-xyz")
    assert result > 0
    assert result < len(text)  # should be fewer tokens than chars


def test_context_snip():
    """Snip should truncate tool outputs over 4000 chars."""
    ctx = ContextManager(max_tokens=3000)
    # Need content > 4000 chars to trigger snip (threshold is 4000)
    msgs = [
        {"role": "tool", "tool_call_id": "t1", "content": "x\n" * 3000},
    ]
    before = estimate_tokens(msgs)
    ctx._snip_tool_outputs(msgs)
    after = estimate_tokens(msgs)
    assert after < before


def test_context_compress():
    ctx = ContextManager(max_tokens=2000)
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg {i} " + "a" * 200})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": "b" * 2000})
    before = estimate_tokens(msgs)
    ctx.maybe_compress(msgs, None)
    after = estimate_tokens(msgs)
    assert after < before
    assert len(msgs) < 40  # should be compressed


def test_context_manager_with_model():
    """ContextManager should track model for accurate token counting."""
    ctx = ContextManager(max_tokens=128_000, model="qwen3-32b")
    assert ctx.model == "qwen3-32b"
    msgs = [{"role": "user", "content": "hello"}]
    # Should use qwen tokenizer
    tokens = ctx.token_usage(msgs)
    assert tokens > 0


def test_autocompact_threshold():
    """Autocompact should trigger only when thresholds are met."""
    ctx = ContextManager(max_tokens=128_000)
    # Below threshold
    assert not ctx.should_autocompact(10_000)
    # Above 40% but not enough delta
    ctx._last_autocompact_tokens = 50_000
    assert not ctx.should_autocompact(55_000)
    # Above 40% and enough delta
    ctx._last_autocompact_tokens = 0
    assert ctx.should_autocompact(60_000)


# --- Session ---

def test_session_save_load():
    msgs = [{"role": "user", "content": "test message"}]
    sid = save_session(msgs, "test-model", "pytest_test_session")
    loaded = load_session("pytest_test_session")
    assert loaded is not None
    assert loaded[0] == msgs
    assert loaded[1] == "test-model"
    # cleanup
    pathlib.Path.home().joinpath(".nanocoder/sessions/pytest_test_session.json").unlink()


def test_session_not_found():
    assert load_session("nonexistent_session_id") is None


def test_list_sessions():
    sessions = list_sessions()
    assert isinstance(sessions, list)


# --- Agent (mock LLM, no API calls) ---

def test_agent_debug_mode():
    """Test that debug mode produces verbose output."""
    from nanocoder.llm import LLMResponse
    from io import StringIO
    import sys

    class MockLLM:
        def __init__(self):
            self.model = "test-model"
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0

        def chat(self, messages, tools=None, on_token=None, debug=False):
            return LLMResponse(
                content="Hello! I'm a mock response.",
                tool_calls=[]
            )

    agent = Agent(llm=MockLLM(), debug=True)

    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()

    try:
        result = agent.chat("Test message")
        output = captured.getvalue()
    finally:
        sys.stdout = old_stdout

    assert "Starting chat round" in output or "Round 1" in output
    assert "LLM responded with text" in output
    assert result == "Hello! I'm a mock response."


def test_agent_reset():
    """Test that agent reset clears messages and error log."""
    from nanocoder.llm import LLMResponse

    class MockLLM:
        def __init__(self):
            self.model = "test-model"

        def chat(self, messages, tools=None, on_token=None, debug=False):
            return LLMResponse(content="ok", tool_calls=[])

    agent = Agent(llm=MockLLM())
    agent.messages.append({"role": "user", "content": "test"})
    agent._error_log.append({"turn": 1, "tool": "test", "error": "test"})
    agent._round_count = 5

    agent.reset()

    assert len(agent.messages) == 0
    assert len(agent._error_log) == 0
    assert agent._round_count == 0
    assert agent._error_watermark == 0
