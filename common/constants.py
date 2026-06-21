# Fixed departments for the restructured VFXPick pipeline.
DEPARTMENTS = ["ROTO", "PAINT", "MM", "COMP"]

USER_ROLES = ["Admin", "Production", "Management", "Supervisor", "Team Lead", "Artist"]
USER_STATUSES = ["Active", "Disabled"]
ARTIST_LEVELS = ["Senior", "Mid", "Junior"]

# Client-facing shot status.
SHOT_STATUSES = ["Hold", "Approved", "Awaiting Approval", "Approved Internal"]

# Supervisor / team lead review status for a shot.
SUPERVISOR_STATUSES = ["Awaiting QC", "Feedback", "Approved", "Hold", "Client FB"]

# Artist work status for a shot.
ARTIST_STATUSES = ["YTS", "In Progress", "WIP Complete", "QC", "Additional"]

# Roles that may view/modify any department (broad access).
BROAD_ACCESS_ROLES = ["Admin", "Production", "Management"]

NOTIFICATION_TYPES = [
    "Task Assigned",
    "QC Submitted",
    "Feedback",
    "New Message",
    "Attachment Shared",
    "Status Updated",
    "System Notification",
]
