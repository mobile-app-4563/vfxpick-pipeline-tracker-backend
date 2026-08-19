# Fixed departments for the restructured VFXPick pipeline.
DEPARTMENTS = ["ROTO", "PAINT", "MM", "COMP"]

# Departments used for user profile/forms; extends pipeline departments.
USER_DEPARTMENTS = [*DEPARTMENTS, "Production", "Management"]

USER_ROLES = ["Admin", "Production", "Management", "Supervisor", "Team Lead", "Artist"]
USER_STATUSES = ["Active", "Disabled"]
ARTIST_LEVELS = ["Senior", "Mid", "Junior"]

# Client-facing shot status (production grid status column).
SHOT_STATUSES = [
    "Hold",
    "Approved",
    "Awaiting Approval",
    "Approved Internal",
    "Bidding",
    "Bids Received",
    "WIP",
    "Delivered",
    "Awaiting Reference",
    "Awaiting Plates",
    "Completed",
    "RTU",
    "Rough Cost Shared",
]

# Supervisor / team lead review status for a shot.
SUPERVISOR_STATUSES = ["Feedback", "Approved", "Hold"]

# Artist work status for a shot.
ARTIST_STATUSES = ["YTS", "In Progress", "Awaiting QC", "WIP Completed", "Render & Upload Completed", "QC", "Additional"]

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
