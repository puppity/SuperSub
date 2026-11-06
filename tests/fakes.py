from subtitle_translator.ai.backend import AIResult, Usage


class FakeBackend:
    name = "fake"
    model = "claude-opus-5-5"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def run_json(self, req):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(req)
        return AIResult(r, Usage(input_tokens=10, output_tokens=5), 0.01)

    def estimate_cost(self, reqs, expected_output_tokens):
        return 0.5

    def check(self):
        return "ok"
