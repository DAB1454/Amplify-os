# Add Workflow

## Description
Creates a new campaign workflow definition with associated prompt templates and wires it into the campaign service.

## Arguments
- `workflow_name` (required): Name of the workflow (e.g., "album-launch", "playlist-push", "merch-drop")
- `phases` (optional): Comma-separated phase names (default: "pre-release,release-day,post-release,evergreen")

## Steps

1. **Validate workflow name**
   - Ensure the name is kebab-case and descriptive
   - Check that a workflow with this name doesn't already exist in `packages/core/workflows/`
   - Confirm the phase names are valid

2. **Generate workflow definition file**
   - Create `packages/core/workflows/{workflow_name}.py`
   - Define the workflow class extending the base Workflow
   - Implement each phase with:
     - Phase name and duration
     - Required actions per phase
     - Channel recommendations
     - Content type specifications
     - Transition conditions to the next phase

3. **Create associated prompt templates**
   - For each phase, create a prompt template in `packages/core/prompts/copywriter/{workflow_name}/`
   - Each template includes: tone guidance, content structure, example copy, variable placeholders
   - Create a `{phase}_brief.md` for each phase with content strategy notes

4. **Wire into campaign service**
   - Register the new workflow in `packages/core/workflows/__init__.py`
   - Add the workflow to the workflow registry/factory
   - Update the campaign service to recognize the new workflow type
   - Add any necessary workflow-specific configuration

5. **Output summary**
   - Display the workflow structure with phases and actions
   - List all created files
   - Show example usage of the workflow in a campaign
   - Suggest testing the workflow with `make test-workflows`
