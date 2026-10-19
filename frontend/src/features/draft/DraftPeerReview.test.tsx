import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {apiRequest} from '../../api/client';
import {usePreferences} from '../../state/preferences';
import {DraftPeerReview,type PeerReviewJob} from './DraftPeerReview';
import {REVIEW_REVISION_POLICY as policy,type RevisionGroup} from './peerReviewTypes';
vi.mock('../../api/client',async original=>({...await original<object>(),apiRequest:vi.fn()}));
beforeEach(()=>{usePreferences.getState().setLanguage('en');vi.mocked(apiRequest).mockResolvedValue({});});
afterEach(()=>{cleanup();vi.resetAllMocks();localStorage.clear();});
function mount(options:{job?:PeerReviewJob;requireSaved?:()=>void;blocked?:boolean;userId?:string}={}) {
  return render(<QueryClientProvider client={new QueryClient({defaultOptions:{mutations:{retry:false}}})}>
    <DraftPeerReview projectId="p" userId="u" revision={7} draftArtifactId="d" sections={[{section_id:'S1',title:'Chapter'}]}
      paragraphs={[{paragraph_id:'p1',paragraph_key:'k',text:'Saved',text_sha256:'a'.repeat(64)}]} blocked={false} requireSaved={()=>{}} refresh={async()=>{}} {...options}/>
  </QueryClientProvider>);
}
function job(result:PeerReviewJob['result'],status='succeeded'):PeerReviewJob{return {id:'review',status,source_draft_artifact_id:'d',progress_current:1,progress_total:1,result};}
const group:RevisionGroup={group_id:'g1',category:'consistency',problem:'Abstract overstates certainty',explanation:'Align with the supported body.',status:'saved',sources:[{ref:'P1:b1',paper_id:'P1',text:'The authors propose coordination.'}],
 patches:[{block_id:'paragraph:abs',before_sha256:'a'.repeat(64),before:'The mechanism is proved.',after:'The mechanism is proposed.',basis:'source',source_refs:['P1:b1'],support_ids:[],operation:'qualify'},
 {block_id:'figure:1',before_sha256:'b'.repeat(64),before:'Figure 1. Coordination.',after:'Figure 1. Proposed coordination.',basis:'source',source_refs:['P1:b1'],support_ids:[],operation:'qualify'}]};
it('first use is optional and makes no model call',()=>{mount();expect(screen.getByRole('button',{name:'Review and revise manuscript'})).toBeEnabled();expect(apiRequest).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'Review only'})).not.toBeInTheDocument();});
it('submits one whole-manuscript revision with author standard and saved identity',async()=>{
 mount();fireEvent.change(screen.getByRole('textbox',{name:'Target journal or article standard',hidden:true}),{target:{value:'Critical chemistry review'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Author guidance',hidden:true}),{target:{value:'Retain the stereochemical qualifications.'}});
 fireEvent.click(screen.getByRole('button',{name:'Review and revise manuscript'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledWith('/api/v1/projects/p/draft/review-jobs',expect.objectContaining({body:JSON.stringify({guidance:'Retain the stereochemical qualifications.',standard:'Critical chemistry review',expected_draft_artifact_id:'d'})})));
});
it('shows one bounded group comparison across body and figure, not an issue repair queue',()=>{
 mount({job:job({policy,revisions:[group,{...group,group_id:'g2',problem:'Reference journal correction',patches:[{...group.patches[0],block_id:'reference:1'}]}],publication:{changed:true,draft_artifact_id:'d'}})});
 expect(screen.getAllByRole('article',{name:'Selected revision group'})).toHaveLength(1);
 expect(screen.getByText('The mechanism is proved.')).toBeInTheDocument();expect(screen.getByText('The mechanism is proposed.')).toBeInTheDocument();
 expect(screen.getByText('Figure 1. Proposed coordination.')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Repair selected finding'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/Reference journal correction/}));
 expect(screen.queryByText('Figure 1. Proposed coordination.')).not.toBeInTheDocument();expect(screen.getByText(/reference:1/)).toBeInTheDocument();
});
it('shows limitations and never calls successful empty processing a saved revision',()=>{
 mount({job:job({policy,outcome:'limited',summary:'No safe correction.',limitations:['Original evidence is missing.'],publication:{changed:false}})});
 expect(screen.getByText('Original evidence is missing.')).toBeInTheDocument();expect(screen.queryByText(/Revised manuscript saved/)).not.toBeInTheDocument();
 expect(screen.getByText(/Run finished without new saved changes/)).toBeInTheDocument();
});
it('keeps historical findings readable without legacy retry or per-finding paid actions',()=>{
 mount({job:job({issues:[{diagnosis:'Old chronology suspicion'}]},'failed')});
 expect(screen.getByText('Historical review (read-only)')).toBeInTheDocument();
 fireEvent.click(screen.getByText('Read original report'));expect(screen.getByText('Old chronology suspicion')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Resume unfinished revision'})).not.toBeInTheDocument();expect(apiRequest).not.toHaveBeenCalled();
});
it('blocks model submission with unsaved editor work or explicit save guard',async()=>{
 const requireSaved=vi.fn(()=>{throw new Error('Unsaved draft');});mount({requireSaved});
 fireEvent.click(screen.getByRole('button',{name:'Review and revise manuscript'}));await waitFor(()=>expect(requireSaved).toHaveBeenCalled());expect(apiRequest).not.toHaveBeenCalled();
});
it('protects retained old review-editor scratch after the UI replacement',async()=>{
 localStorage.setItem('rw-draft-scratch:u:p:k:review-manual',JSON.stringify({text:'Unsaved old editor text',hash:'a'.repeat(64),revision:7}));mount();
 fireEvent.click(screen.getByRole('button',{name:'Review and revise manuscript'}));await waitFor(()=>expect(screen.getByRole('alert')).toBeInTheDocument());expect(apiRequest).not.toHaveBeenCalled();
});
it('uses checkpoint retry only for a new failed run with the same draft',async()=>{
 mount({job:job({policy,phase:'audit'},'interrupted')});fireEvent.click(screen.getByRole('button',{name:'Resume unfinished revision'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledWith('/api/v1/jobs/review/retry',expect.objectContaining({method:'POST'})));
});
it('retains unsaved guidance separately per account and project',()=>{
 const view=mount();fireEvent.change(screen.getByRole('textbox',{name:'Author guidance',hidden:true}),{target:{value:'Keep my author input'}});view.unmount();
 const other=mount({userId:'another'});expect(screen.getByRole('textbox',{name:'Author guidance',hidden:true})).toHaveValue('');other.unmount();
 mount();expect(screen.getByRole('textbox',{name:'Author guidance',hidden:true})).toHaveValue('Keep my author input');expect(apiRequest).not.toHaveBeenCalled();
});
it('marks old-version results and disables unsafe continuation',()=>{
 mount({job:{...job({policy,phase:'audit'},'failed'),source_draft_artifact_id:'older'}});
 expect(screen.getByText(/older manuscript/)).toBeInTheDocument();expect(screen.getByRole('button',{name:'Resume unfinished revision'})).toBeDisabled();
});
it('retains old unsent author instructions without leaking another account or replaying them',()=>{
 localStorage.setItem('rw-draft-scratch:u:p:review-help:old-issue',JSON.stringify('My unsent guidance'));
 localStorage.setItem('rw-draft-scratch:other:p:review-help:old-issue',JSON.stringify('Other account private input'));
 mount();fireEvent.click(screen.getByText('Retained author input from the old reviewer'));
 expect(screen.getByRole('textbox',{name:'Retained input 1'})).toHaveValue('My unsent guidance');
 expect(screen.queryByDisplayValue('Other account private input')).not.toBeInTheDocument();expect(apiRequest).not.toHaveBeenCalled();
});
