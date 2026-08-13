# Daily Report audit schema

Return a single JSON object. Do not wrap it in Markdown.

```json
{
  "project_groups": [
    {
      "name": "Readable project name",
      "project_ids": ["registered_project_id"],
      "tasks": [
        {
          "title": "Readable task or experiment title",
          "did": ["Concrete action"],
          "why": ["Reason explicitly present in evidence"],
          "results": ["Test, metric, artifact, or explicit conclusion"],
          "files": ["Relevant file path from evidence"],
          "evidence": ["exact allowed evidence ref"],
          "confidence": "observed|reported|inferred|confirmed"
        }
      ]
    }
  ],
  "plan_closure": [
    {
      "plan": "Previous plan text",
      "status": "completed|partially_completed|blocked|deferred|no_evidence|cancelled",
      "reason": "Evidence-bound reason",
      "evidence": ["exact allowed evidence ref"]
    }
  ],
  "knowledge": [
    {
      "text": "Reusable conclusion",
      "scope": "Dataset, model, environment, or other applicability boundary",
      "evidence": ["exact allowed evidence ref"],
      "confidence": "observed|reported|inferred|confirmed"
    }
  ],
  "blockers": [
    {
      "text": "Concrete blocker",
      "project": "registered_project_id",
      "next": "Action that could remove it",
      "evidence": ["exact allowed evidence ref"],
      "confidence": "observed|reported|inferred|confirmed"
    }
  ],
  "next_actions": [
    {
      "action": "Concrete next action",
      "project": "registered_project_id",
      "acceptance": "Observable completion condition",
      "basis": ["exact allowed evidence ref"]
    }
  ],
  "unknown_updates": [],
  "blocker_updates": [],
  "breakthroughs": [],
  "project_updates": [],
  "source_coverage": [
    {
      "ref": "exact coverage_required_refs ref",
      "status": "core_task|merged|supporting|non_substantive|insufficient_summary",
      "task_title": "Exact generated task title for core_task or merged",
      "reason": "Required for supporting, non_substantive, or insufficient_summary"
    }
  ],
  "data_quality": ["Explicit missing or uncertain data"]
}
```

## Intelligence fields

Populate these fields only when the same evidence supports them; they feed the
project-intelligence views without another model call.

- `unknown_updates`: one project ID, a genuine unanswered research question,
  `open|update|resolve`, priority, missing evidence, optional prior ID,
  evidence, and confidence. Ordinary todo items are not unknowns.
- `blocker_updates`: one project ID, a blocker, `open|update|resolve`, priority,
  missing evidence, optional prior ID, evidence, and confidence.
- `breakthroughs`: one project ID, title, material change, significance,
  evidence, and confidence. Ordinary implementation or commits are not
  breakthroughs.
- `project_updates`: at most one complete sentence per active project,
  summarizing its problem, action, result, and changed understanding.

Use the precise field names already demonstrated by any `output_schema` or
examples embedded in the audit bundle. If the bundle and this reference differ,
the bundle and validator are authoritative.

## Coverage audit

Every entry in `coverage_required_refs` must appear exactly once in
`source_coverage`.

- `core_task`: this fragment directly supports a generated task.
- `merged`: duplicate evidence for the same generated task.
- `supporting`: useful context that does not justify a separate task.
- `non_substantive`: greeting, navigation, pure question, or unrelated chatter.
- `insufficient_summary`: activity exists but the retained excerpt cannot
  support a responsible claim.

For `core_task` and `merged`, `task_title` must exactly equal a title in
`project_groups[].tasks[]`. Do not merge different goals merely because they
belong to the same project.
