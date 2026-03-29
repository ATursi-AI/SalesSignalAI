"""
Base agent class implementing the Think -> Act -> Observe -> Decide loop.
All agents inherit from this.
"""
import json
import logging
from datetime import datetime

logger = logging.getLogger('agents')


class BaseAgent:
    name = "BaseAgent"
    description = "Base agent"
    model = "claude-sonnet-4-20250514"
    max_steps = 15

    def __init__(self):
        self.tools = {}
        self.mission_log = []
        self.status = "idle"
        self._register_tools()

    def _register_tools(self):
        pass

    # ── LLM integration ──────────────────────────────────────────────

    def _call_llm(self, messages, available_tools=None):
        if 'claude' in self.model or 'sonnet' in self.model or 'haiku' in self.model or 'opus' in self.model:
            return self._call_claude(messages, available_tools)
        elif 'deepseek' in self.model:
            return self._call_deepseek(messages)
        elif 'gpt' in self.model:
            return self._call_openai(messages)
        raise ValueError(f"Unsupported model: {self.model}")

    def _call_claude(self, messages, available_tools):
        import anthropic
        from django.conf import settings

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        tool_defs = []
        if available_tools:
            for name, info in available_tools.items():
                tool_defs.append({
                    "name": name,
                    "description": info["description"],
                    "input_schema": info.get("schema", {"type": "object", "properties": {}}),
                })

        # Extract system message from messages list
        system_msg = ""
        filtered_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_msg = m["content"]
            else:
                filtered_messages.append(m)
        kwargs = {"model": self.model, "max_tokens": 4096, "messages": filtered_messages}
        if system_msg:
            kwargs["system"] = system_msg
        if tool_defs:
            kwargs["tools"] = tool_defs

        response = client.messages.create(**kwargs)
        return self._parse_claude_response(response)

    def _call_deepseek(self, messages):
        import openai
        from django.conf import settings
        client = openai.OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        r = client.chat.completions.create(model="deepseek-chat", messages=messages, max_tokens=4096)
        return {"type": "text", "content": r.choices[0].message.content, "done": True, "tool_calls": []}

    def _call_openai(self, messages):
        import openai
        from django.conf import settings
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        r = client.chat.completions.create(model=self.model, messages=messages, max_tokens=4096)
        return {"type": "text", "content": r.choices[0].message.content, "done": True, "tool_calls": []}

    def _parse_claude_response(self, response):
        result = {"type": "text", "content": "", "tool_calls": [], "done": False}
        for block in response.content:
            if block.type == "text":
                result["content"] += block.text
            elif block.type == "tool_use":
                result["type"] = "tool_use"
                result["tool_calls"].append({"id": block.id, "name": block.name, "input": block.input})
        result["done"] = response.stop_reason == "end_turn"
        return result

    # ── Tool execution ───────────────────────────────────────────────

    def _execute_tool(self, tool_name, tool_input):
        if tool_name not in self.tools:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return self.tools[tool_name]["function"](**tool_input)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"error": str(e)}

    # ── Logging ──────────────────────────────────────────────────────

    def log(self, message, level="info"):
        entry = {"timestamp": datetime.now().isoformat(), "agent": self.name, "level": level, "message": message}
        self.mission_log.append(entry)
        getattr(logger, level)(f"[{self.name}] {message}")

    # ── Main loop ────────────────────────────────────────────────────

    def run(self, goal, mission_id=None):
        self.status = "running"
        self.log(f"Starting mission: {goal}")

        system_prompt = (
            f"You are {self.name}, a specialized AI agent.\n{self.description}\n\n"
            f"Your goal: {goal}\n\n"
            "Rules:\n"
            "- Use tools to accomplish the goal\n"
            "- Be efficient, minimize unnecessary steps\n"
            "- When done, summarize findings clearly\n"
            "- Never fabricate data\n"
            "- If a tool fails, try alternatives"
        )

        messages = [{"role": "user", "content": goal}]
        available_tools = {
            n: {"description": i["description"], "schema": i.get("schema", {"type": "object", "properties": {}})}
            for n, i in self.tools.items()
        }

        for step in range(self.max_steps):
            self.log(f"Step {step + 1}/{self.max_steps}")
            try:
                response = self._call_llm(
                    [{"role": "system", "content": system_prompt}] + messages,
                    available_tools=available_tools,
                )
            except Exception as e:
                self.log(f"LLM call failed: {e}", "error")
                self.status = "error"
                return f"Agent error: {e}"

            if response.get("done") or response["type"] == "text":
                self.status = "complete"
                self.log(f"Mission complete in {step + 1} steps")
                return response["content"]

            if response["type"] == "tool_use":
                assistant_content = []
                if response.get("content"):
                    assistant_content.append({"type": "text", "text": response["content"]})
                for tc in response["tool_calls"]:
                    assistant_content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
                messages.append({"role": "assistant", "content": assistant_content})

                tool_results = []
                for tc in response["tool_calls"]:
                    self.log(f"Tool: {tc['name']}({json.dumps(tc['input'])[:200]})")
                    result = self._execute_tool(tc["name"], tc["input"])
                    result_str = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
                    self.log(f"Result: {result_str[:200]}")
                    tool_results.append({"type": "tool_result", "tool_use_id": tc["id"], "content": result_str[:10000]})
                messages.append({"role": "user", "content": tool_results})

        self.status = "max_steps"
        self.log("Hit max steps limit", "warning")
        return "Agent reached maximum steps. Partial results in mission log."
