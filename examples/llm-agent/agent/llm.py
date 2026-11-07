"""The OpenAI Responses API call. gpt-6.1-terra supports tool calling only through this API."""


class OpenAIResponder:
    def __init__(self, model, reasoning_effort, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    def respond(self, instructions, items, tools):
        return self._client.responses.create(
            model=self._model,
            instructions=instructions,
            input=items,
            tools=tools,
            parallel_tool_calls=False,
            reasoning={"effort": self._reasoning_effort},
        )
