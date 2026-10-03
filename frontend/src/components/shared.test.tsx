import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Status, Pagination, Feedback } from "./shared";
describe("critical states", () => {
  it("distinguishes failures from published content", () => {
    expect(renderToStaticMarkup(<Status value="failed" />)).toContain("bad");
    expect(renderToStaticMarkup(<Status value="published" />)).toContain(
      "good",
    );
    expect(renderToStaticMarkup(<Status value="unknown_outcome" />)).toContain(
      "unknown outcome",
    );
  });
  it("disables pagination when there are no other pages", () => {
    expect(
      renderToStaticMarkup(
        <Pagination page={1} total={0} setPage={() => {}} />,
      ).match(/disabled=""/g)?.length,
    ).toBe(2);
  });
  it("renders retry with an accessible error", () => {
    const html = renderToStaticMarkup(
      <Feedback
        loading={false}
        error={new Error("Offline")}
        retry={() => {}}
      />,
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("Offline");
    expect(html).toContain("Thử lại");
  });
});
