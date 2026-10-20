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
function mount(options:{job?:PeerReviewJob;requireSaved?:()=>void;blocked?:boolean;userId?:string;
  authorApprovedReviewJobId?:string;authorApprovedReviewGroupIds?:string[];
  runs?:{id:string;status:string;created_at:string;source_draft_artifact_id:string;outcome:string;current:boolean;pending_group_count:number}[];
  onSelectRun?:(id:string)=>void}={}) {
  return render(<QueryClientProvider client={new QueryClient({defaultOptions:{mutations:{retry:false}}})}>
    <DraftPeerReview projectId="p" userId="u" revision={7} draftArtifactId="d" sections={[{section_id:'S1',title:'Chapter'}]}
      paragraphs={[{paragraph_id:'p1',paragraph_key:'k',text:'Saved',text_sha256:'a'.repeat(64)}]} blocked={false} requireSaved={()=>{}} refresh={async()=>{}} {...options}/>
  </QueryClientProvider>);
}
function job(result:PeerReviewJob['result'],status='succeeded'):PeerReviewJob{return {id:'review',status,source_draft_artifact_id:'d',progress_current:1,progress_total:1,result};}
const group:RevisionGroup={group_id:'g1',category:'consistency',problem:'Abstract overstates certainty',explanation:'Align with the supported body.',status:'saved',sources:[{ref:'P1:b1',paper_id:'P1',text:'The authors propose coordination.'}],
 patches:[{block_id:'paragraph:abs',before_sha256:'a'.repeat(64),before:'The mechanism is proved.',after:'The mechanism is proposed.',basis:'source',source_refs:['P1:b1'],support_ids:[],operation:'qualify'},
 {block_id:'figure:1',before_sha256:'b'.repeat(64),before:'Figure 1. Coordination.',after:'Figure 1. Proposed coordination.',basis:'source',source_refs:['P1:b1'],support_ids:[],operation:'qualify'}]};
it('lists recent review runs and switches the selected report',()=>{
 const onSelectRun=vi.fn();
 mount({job:job({policy,revisions:[]}),onSelectRun,runs:[
   {id:'review',status:'succeeded',created_at:'2026-10-20T10:00:00Z',source_draft_artifact_id:'d',outcome:'no_change_needed',current:true,pending_group_count:0},
   {id:'older',status:'succeeded',created_at:'2026-10-19T10:00:00Z',source_draft_artifact_id:'old',outcome:'limited',current:false,pending_group_count:2}]});
 fireEvent.change(screen.getByRole('combobox',{name:'Review run'}),{target:{value:'older'}});
 expect(onSelectRun).toHaveBeenCalledWith('older');
});

it('first use is optional and makes no model call',()=>{mount();expect(screen.getByRole('button',{name:'Review and generate revision candidates'})).toBeEnabled();expect(apiRequest).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'Review only'})).not.toBeInTheDocument();});
it('submits one whole-manuscript revision with author standard and saved identity',async()=>{
 mount();fireEvent.change(screen.getByRole('textbox',{name:'Target journal or article standard',hidden:true}),{target:{value:'Critical chemistry review'}});
 fireEvent.change(screen.getByRole('textbox',{name:'Author guidance',hidden:true}),{target:{value:'Retain the stereochemical qualifications.'}});
 fireEvent.click(screen.getByRole('button',{name:'Review and generate revision candidates'}));
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
it('shows an existing before/after candidate and saves only after explicit author acceptance',async()=>{
 mount({job:job({policy,outcome:'limited',publication:{changed:false},revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)}]})});
 expect(screen.getByText('The mechanism is proved.')).toBeInTheDocument();
 expect(screen.getByText('The mechanism is proposed.')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Accept this revision'})).toBeEnabled();
 expect(apiRequest).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Accept this revision'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledTimes(1));
 expect(apiRequest).toHaveBeenCalledWith('/api/v1/projects/p/draft/review-candidates/review/g1/accept',
   expect.objectContaining({body:JSON.stringify({expected_draft_artifact_id:'d',candidate_sha256:'c'.repeat(64)})}));
 expect(screen.queryByRole('button',{name:/Generate an AI suggestion/})).not.toBeInTheDocument();
});
it('records a rejection without claiming the manuscript changed',async()=>{
 mount({job:job({policy,outcome:'awaiting_author_choice',publication:{changed:false},
   revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)}]})});
 fireEvent.click(screen.getByRole('button',{name:'Reject; keep original'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledWith('/api/v1/projects/p/draft/review-candidates/review/g1/reject',
   expect.objectContaining({body:JSON.stringify({expected_draft_artifact_id:'d',candidate_sha256:'c'.repeat(64)})})));
});

it('accepts all pending candidates as one selected batch',async()=>{
 const next={...group,group_id:'g2',problem:'Second pending edit',status:'not_saved' as const,
   candidate_sha256:'e'.repeat(64),patches:[{...group.patches[0],block_id:'paragraph:con'}]};
 mount({job:job({policy,outcome:'awaiting_author_choice',publication:{changed:false},revisions:[
   {...group,status:'not_saved',candidate_sha256:'c'.repeat(64)},next]})});
 fireEvent.click(screen.getByRole('button',{name:'Accept all pending (2)'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledWith('/api/v1/projects/p/draft/review-candidates/review/accept-selected',
   expect.objectContaining({body:JSON.stringify({expected_draft_artifact_id:'d',groups:[
     {group_id:'g1',candidate_sha256:'c'.repeat(64)},{group_id:'g2',candidate_sha256:'e'.repeat(64)}]})})));
});

it('warns about a historically audit-failed candidate and excludes it from batch acceptance',()=>{
 mount({job:job({policy,outcome:'limited',revisions:[{...group,status:'not_saved',audit_failed:true,candidate_sha256:'c'.repeat(64)}]})});
 expect(screen.getByText(/historical independent audit did not pass/)).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/Accept all pending/})).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Accept this revision'})).toBeEnabled();
});

it('shows each group, but never invents an approval button when there is no concrete candidate',()=>{
 mount({job:job({policy,outcome:'limited',publication:{changed:false},revisions:[
   {...group,status:'not_saved',candidate_sha256:'c'.repeat(64)},
   {...group,group_id:'g2',status:'no_candidate',problem:'Missing AI response',explanation:'No concrete proposal',patches:[],sources:[],candidate_sha256:''}]})});
 fireEvent.click(screen.getByRole('button',{name:/Missing AI response/}));
 expect(screen.getByText('No concrete proposal')).toBeInTheDocument();
 expect(screen.getByText(/nothing to accept/)).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Accept this revision'})).not.toBeInTheDocument();
 expect(apiRequest).not.toHaveBeenCalled();
});
it('shows a dismissed review group as no change needed without an approval button',()=>{
 mount({job:job({policy,outcome:'no_change_needed',publication:{changed:false},revisions:[{
   ...group,status:'dismissed',explanation:'The existing sentence already limits the claim.',patches:[],sources:[],candidate_sha256:''}]})});
 expect(screen.getByText('No change needed after review')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/Abstract overstates certainty/}));
 expect(screen.getByText('The existing sentence already limits the claim.')).toBeInTheDocument();
 expect(screen.getByText('No manuscript change was needed for this group.')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Accept this revision'})).not.toBeInTheDocument();
});

it('reports pending review candidates and asks before switching to a new review',()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 mount({job:{...job({policy,outcome:'awaiting_author_choice',publication:{changed:false},
   revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)}]}),pending_group_count:1}});
 expect(screen.getByText('1 suggestion awaits your decision; the manuscript is unchanged.')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Review and generate revision candidates'}));
 expect(confirm).toHaveBeenCalled();
 expect(apiRequest).not.toHaveBeenCalled();
 confirm.mockRestore();
});

it('keeps old unsent PDF input owned and readable without reviving the old approval workflow',()=>{
 localStorage.setItem('rw-draft-scratch:u:p:review-pdf:review:g1:P001:transcription',JSON.stringify('Retained note'));
 mount({userId:'other'});expect(screen.queryByText('Retained note')).not.toBeInTheDocument();
 cleanup();mount();fireEvent.click(screen.getByText('Retained author input from the old reviewer'));
 expect(screen.getByDisplayValue('Retained note')).toBeInTheDocument();
 expect(apiRequest).not.toHaveBeenCalled();
});
it('disables adoption from an old draft even when a candidate still exists',()=>{
 mount({job:{...job({policy,revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)}]}),source_draft_artifact_id:'older'}});
 expect(screen.getByRole('button',{name:'Accept this revision'})).toBeDisabled();
});
it('allows the next independent group after a prior acceptance, never re-accepting the first group',async()=>{
 const next={...group,group_id:'g2',problem:'Second independent edit',status:'not_saved' as const,
   candidate_sha256:'e'.repeat(64),patches:[{...group.patches[0],block_id:'paragraph:con'}]};
 mount({job:{...job({policy,revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)},next]}),
   source_draft_artifact_id:'older'},authorApprovedReviewJobId:'review',authorApprovedReviewGroupIds:['g1']});
 expect(screen.getByText('Accepted and saved')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Accept this revision'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/Second independent edit/}));
 fireEvent.click(screen.getByRole('button',{name:'Accept this revision'}));
 await waitFor(()=>expect(apiRequest).toHaveBeenCalledWith('/api/v1/projects/p/draft/review-candidates/review/g2/accept',
   expect.objectContaining({body:JSON.stringify({expected_draft_artifact_id:'d',candidate_sha256:'e'.repeat(64)})})));
});
it('shows limitations and never calls successful empty processing a saved revision',()=>{
 mount({job:job({policy,outcome:'limited',summary:'No safe correction.',limitations:['Original evidence is missing.'],publication:{changed:false}})});
 expect(screen.getByText('Original evidence is missing.')).toBeInTheDocument();expect(screen.queryByText(/Revised manuscript saved/)).not.toBeInTheDocument();
 expect(screen.getByText(/This run did not deliver a safely adoptable revision/)).toBeInTheDocument();
});
it('keeps historical findings readable without legacy retry or per-finding paid actions',()=>{
 mount({job:job({issues:[{diagnosis:'Old chronology suspicion'}]},'failed')});
 expect(screen.getByText('Historical review (read-only)')).toBeInTheDocument();
 fireEvent.click(screen.getByText('Read original report'));expect(screen.getByText('Old chronology suspicion')).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Resume unfinished revision'})).not.toBeInTheDocument();expect(apiRequest).not.toHaveBeenCalled();
});
it('blocks model submission with unsaved editor work or explicit save guard',async()=>{
 const requireSaved=vi.fn(()=>{throw new Error('Unsaved draft');});mount({requireSaved});
 fireEvent.click(screen.getByRole('button',{name:'Review and generate revision candidates'}));await waitFor(()=>expect(requireSaved).toHaveBeenCalled());expect(apiRequest).not.toHaveBeenCalled();
});
it('protects retained old review-editor scratch after the UI replacement',async()=>{
 localStorage.setItem('rw-draft-scratch:u:p:k:review-manual',JSON.stringify({text:'Unsaved old editor text',hash:'a'.repeat(64),revision:7}));mount();
 fireEvent.click(screen.getByRole('button',{name:'Review and generate revision candidates'}));await waitFor(()=>expect(screen.getByRole('alert')).toBeInTheDocument());expect(apiRequest).not.toHaveBeenCalled();
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
it('does not resume a stopped review after part of its candidates was accepted',()=>{
 mount({job:{...job({policy,phase:'propose',revisions:[{...group,status:'not_saved',candidate_sha256:'c'.repeat(64)}]},'cancelled'),
   source_draft_artifact_id:'older'},authorApprovedReviewJobId:'review',authorApprovedReviewGroupIds:['g1']});
 expect(screen.queryByRole('button',{name:'Resume unfinished revision'})).not.toBeInTheDocument();
 expect(screen.getByText(/remaining candidates can still be accepted one by one/)).toBeInTheDocument();
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
