import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EXPORT =
  '{"case_id":"a/b-1","username":"me+you@example.com","submitted_at":"2026-10-23T14:05:09Z",' +
  '"updated_at":"2026-10-23T14:05:09Z","version":1,"offset_unit":"unicode-code-point",' +
  '"record":{"case_id":"a/b-1","username":"me+you@example.com","narrative":"Aspirin caused nausea."}}';

/** Answers the status probe itself so a test's ordered responses go to the case routes. */
function stubFetch(...responses: Response[]) {
  const ordered = vi.fn<(url: string) => Promise<Response>>();
  for (const response of responses) ordered.mockResolvedValueOnce(response);
  vi.stubGlobal("fetch", (url: string) =>
    url === "/api/v1/llm" ? Promise.resolve(json(200, { enabled: false, provider: null, model: null })) : ordered(url),
  );
  return ordered;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("lists a username's cases and opens one", async () => {
    const fetchMock = stubFetch(
        json(200, {
          username: "me+you@example.com",
          submissions: [
            { case_id: "a/b-1", submitted_at: "t", updated_at: "t", version: 1 },
            { case_id: "7-2", submitted_at: "t", updated_at: "2026-10-27T09:00:00Z", version: 3 },
          ],
        }),
      new Response(EXPORT, { status: 200 }),
    );
    render(<App />);
    await userEvent.type(screen.getByLabelText("Username"), "me+you@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Show cases" }));
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/submissions?username=me%2Byou%40example.com",
    );
    expect(await screen.findByText("Not saved yet")).toBeInTheDocument();
    expect(window.localStorage.getItem("scat.username")).toBe("me+you@example.com");

    await userEvent.click(screen.getByRole("button", { name: "a/b-1" }));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/submissions/a%2Fb-1?username=me%2Byou%40example.com",
    );
    expect(await screen.findByRole("heading", { name: "a/b-1" })).toBeInTheDocument();
    expect(screen.getByText("Aspirin caused nausea.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "All cases" }));
    expect(screen.getByLabelText("Username")).toHaveValue("me+you@example.com");
  });

  it("explains an empty list and a failed one", async () => {
    stubFetch(
      json(200, { username: "nobody", submissions: [] }),
      json(422, { error: { code: "INVALID_PARAMETERS" } }),
    );
    render(<App />);
    const input = screen.getByLabelText("Username");
    await userEvent.type(input, "nobody");
    await userEvent.click(screen.getByRole("button", { name: "Show cases" }));
    expect(await screen.findByText(/No cases are stored for this username/)).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "not valid");
    await userEvent.click(screen.getByRole("button", { name: "Show cases" }));
    expect(await screen.findByText(/That username isn't valid/)).toBeInTheDocument();
  });
});
