# Remaining Legacy handoff gates

These are verified source findings, not an assertion that every owner-named
field needs replacement. Current owner==entity deployments are not affected by
these future handoff cases; the handoff entry must remain closed until resolved.

| System/module | Verified path | Handoff issue / required follow-up |
| --- | --- | --- |
| Backend Legacy Skill | adapters/http/skill_center/skills.py::_get_path_params | entity input defaults to caller and is returned as a path locator without Bot canonicalization; metadata lookup still uses strict current Owner |
| Backend Legacy SkillSet | adapters/http/skill_center/skillsets.py::_get_path_params | Same separate request-to-resource conversion remains; current Owner B must not become storage entity B |
| Backend Legacy upload | skills.py::upload_skill | Direct get_by_id_and_owner(effective_bot_id, owner locator) before LocalSkillUploadService can reject an entity-addressed request after Owner changes |
| Backend Legacy delete | skills.py local asset delete branches | Stored Skill user_id is an entity locator but is passed to strict current-Owner lookup; resolve the persisted Bot and authorize actual actor separately |
| Backend Legacy Skill factories | core/skill_center/factories.py::SkillServiceFactory.create | Pool path resolution uses lookup_owner_id despite explicit entity contract; audit metadata/current-owner lookup separately from canonical storage paths |
| Backend Legacy SkillSet factories | factories.py::SkillSetServiceFactory.create | Several user_id-or-entity_id precedence points conflate actor/metadata locator/resource entity; trace actual callers before altering meaning |
| Backend mixed resources | Render Screen, Session Resource uploader/ownership, active work-order and notification state | Not pure Bot locator columns; preserve people and historical facts, decide necessary field split and transfer policy in mixed-field stage |

The HTTP Skill and SkillSet modules are currently larger than the repository's
1,000-line source-file rule. Completing their changes requires a scoped split
with route-order, import/patch, interceptor, DTO and endpoint compatibility tests;
this batch did not introduce an allowlist exemption or change their behavior.
Do not make a blanket get_by_id_and_owner semantic change or translate all
user_id values to entity at the factory/persistence layer to avoid that work.
