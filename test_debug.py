from sentinel_ai.workflows.meeting_intel import create_meeting_workflow
_, tasks = create_meeting_workflow({})
print("TASKS COUNT:", len(tasks))
for t in tasks:
    print(t.id)
