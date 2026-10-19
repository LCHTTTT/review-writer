import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {MemoryRouter} from "react-router-dom";
import {afterEach, describe, expect, it, vi} from "vitest";
import {AttachmentPicker} from "./AttachmentPicker";

const upload = vi.hoisted(() => vi.fn());
vi.mock("../library/sharedUploads", () => ({useSharedUploads: () => ({store: {upload}, rows: []})}));
afterEach(() => {cleanup(); vi.restoreAllMocks(); upload.mockReset();});
function setup(ids: string[] = []) {
  const client = new QueryClient({defaultOptions:{queries:{retry:false,staleTime:Infinity}}});
  client.setQueryData(["me"], {user_id:"fixture"});
  const onChange = vi.fn();
  render(<QueryClientProvider client={client}><MemoryRouter><AttachmentPicker ids={ids} onChange={onChange} compact /></MemoryRouter></QueryClientProvider>);
  return onChange;
}
describe("conversation upload icon", () => {
  it("opens the real file input without performing an upload on icon click", () => {
    setup();
    const input = screen.getByLabelText("上传对话附件");
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", {name:"上传文献或附件"}));
    expect(click).toHaveBeenCalledOnce();
    expect(upload).not.toHaveBeenCalled();
    expect(screen.getByText("PDF 解析可能计费")).toBeVisible();
  });
  it("retains existing attachments and uses the shared upload path", async () => {
    upload.mockResolvedValue({id:"new-file"});
    const onChange = setup(["existing"]);
    const file = new File(["PDF"], "paper.pdf", {type:"application/pdf"});
    fireEvent.change(screen.getByLabelText("上传对话附件"), {target:{files:[file]}});
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(["existing","new-file"]));
    expect(upload).toHaveBeenCalledWith(file);
  });
  it("keeps the original attachment limit", async () => {
    setup(Array.from({length:8}, (_,i)=>String(i)));
    fireEvent.change(screen.getByLabelText("上传对话附件"), {target:{files:[new File(["text"],"notes.txt")]}});
    await screen.findByText("每条消息最多 8 个附件。");
    expect(upload).not.toHaveBeenCalled();
  });
});
