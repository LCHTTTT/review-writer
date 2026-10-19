import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {MemoryRouter, useLocation} from "react-router-dom";
import {DiscussionPicker} from "./DiscussionPicker";
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function Location(){return <output>{useLocation().search}</output>;}
it("keeps existing history accessible without redundant discussion management or write requests",async()=>{
  const fetch=vi.fn(async()=>Response.json({items:[{id:"one",title:"已有对话",archived:false}]}));
  vi.stubGlobal("fetch",fetch);
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/conversation?project=p"]}><DiscussionPicker endpoint="/api/v1/projects/p/conversation" sessionId=""/><Location/></MemoryRouter></QueryClientProvider>);
  await screen.findByRole("option",{name:"已有对话"});
  expect(screen.queryByRole("button",{name:"新建讨论"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"重命名"})).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox",{name:"选择对话"}),{target:{value:"one"}});
  expect(screen.getByText("?project=p&session=one")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
