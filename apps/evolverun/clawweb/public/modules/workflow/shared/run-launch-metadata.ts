/** Stored launch input and optional database lineage when supplied by the run projection. */
export interface RunLaunchMetadata {
  params_json: string | null;
  input_json: string | null;
  parent_flow_id: string | null;
  root_flow_id: string | null;
}
