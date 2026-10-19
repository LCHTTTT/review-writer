export const REVIEW_REVISION_POLICY = 'review-revision/2';
export type ReviewSource = {ref:string; paper_id:string; title?:string; page?:number; text:string; source_hash?:string};
export type RevisionPatch = {block_id:string; before_sha256:string; before:string; after:string; basis:'source'|'manuscript'; source_refs:string[]; support_ids:string[]; operation:string};
export type RevisionGroup = {group_id:string; category:string; problem:string; explanation:string; status:'saved'|'not_saved'; patches:RevisionPatch[]; sources:ReviewSource[]};
export type PeerReviewJob = {
  id:string; status:string; progress_current:number; progress_total:number; error_message?:string; source_draft_artifact_id?:string;
  result?: {policy?:string; phase?:string; progress?:{groups_completed:number; groups_total:number; verified_groups:number; planning_completed?:number; planning_processed?:number; planning_failed?:number; planning_total?:number; active_model_calls?:number};
    outcome?:'revised'|'revised_with_limits'|'limited'|'no_change_needed'; summary?:string; limitations?:string[];
    publication?:{changed:boolean; draft_artifact_id?:string; saved_groups?:number; saved_objects?:number}; revisions?:RevisionGroup[];
    // Display-only compatibility; no legacy execution/repair controls.
    issues?:{issue_id?:string; diagnosis:string; status?:string}[]; review_status?:string;
    review_diagnostics?:{message:string; diagnosis?:string; status?:string}[];
  };
};
