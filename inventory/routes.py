from flask import Blueprint, request
from auth.middleware import token_required
from common.db_utils import get_user, run_query
from common.http import failure, success
from common.constants import BROAD_ACCESS_ROLES

inventory_bp = Blueprint("inventory", __name__)

def _ensure_table():
    run_query(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INT AUTO_INCREMENT PRIMARY KEY,
            item_name VARCHAR(150) NOT NULL,
            category VARCHAR(50) NOT NULL,
            serial_number VARCHAR(100) UNIQUE,
            status ENUM('Available', 'Assigned', 'Maintenance', 'Retired') NOT NULL DEFAULT 'Available',
            assigned_to_user_id VARCHAR(20) DEFAULT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (assigned_to_user_id) REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

def _is_authorized(user):
    return user and user.get("role") in BROAD_ACCESS_ROLES

@inventory_bp.route("", methods=["GET"])
@token_required
def list_items(current_user_id):
    _ensure_table()
    rows = run_query(
        """
        SELECT i.*, u.name AS assigned_to_name
        FROM inventory i
        LEFT JOIN users u ON i.assigned_to_user_id = u.user_id
        ORDER BY i.id DESC
        """,
        fetch_all=True
    ) or []
    
    # Format datetimes
    formatted = []
    for r in rows:
        formatted.append({
            "id": r["id"],
            "itemName": r["item_name"],
            "category": r["category"],
            "serialNumber": r["serial_number"],
            "status": r["status"],
            "assignedToUserId": r["assigned_to_user_id"],
            "assignedToName": r["assigned_to_name"],
            "notes": r["notes"],
            "createdAt": r["created_at"].isoformat() if r.get("created_at") else None,
            "updatedAt": r["updated_at"].isoformat() if r.get("updated_at") else None,
        })
    return success({"inventory": formatted})

@inventory_bp.route("/users", methods=["GET"])
@token_required
def list_assignable_users(current_user_id):
    rows = run_query(
        "SELECT user_id, name, department, role FROM users WHERE status = 'Active' ORDER BY name",
        fetch_all=True
    ) or []
    return success({
        "users": [
            {
                "userId": r["user_id"],
                "name": r["name"],
                "department": r["department"],
                "role": r["role"]
            }
            for r in rows
        ]
    })

@inventory_bp.route("", methods=["POST"])
@token_required
def create_item(current_user_id):
    user = get_user(current_user_id)
    if not _is_authorized(user):
        return failure("Only Admin, Production, or Management can add inventory.", 403)
        
    _ensure_table()
    data = request.get_json(silent=True) or {}
    item_name = data.get("itemName")
    category = data.get("category")
    serial_number = data.get("serialNumber") or None
    status = data.get("status") or "Available"
    assigned_to = data.get("assignedToUserId") or None
    notes = data.get("notes") or ""
    
    if not item_name or not category:
        return failure("Item name and category are required.", 400)
        
    try:
        run_query(
            """
            INSERT INTO inventory (item_name, category, serial_number, status, assigned_to_user_id, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (item_name, category, serial_number, status, assigned_to, notes)
        )
        return success({"message": "Inventory item created successfully."})
    except Exception as e:
        if "Duplicate entry" in str(e) or "serial_number" in str(e):
            return failure("An item with this serial number already exists.", 400)
        return failure(str(e), 500)

@inventory_bp.route("/<int:item_id>", methods=["PUT"])
@token_required
def update_item(current_user_id, item_id):
    user = get_user(current_user_id)
    if not _is_authorized(user):
        return failure("Only Admin, Production, or Management can update inventory.", 403)
        
    _ensure_table()
    data = request.get_json(silent=True) or {}
    item_name = data.get("itemName")
    category = data.get("category")
    serial_number = data.get("serialNumber") or None
    status = data.get("status")
    assigned_to = data.get("assignedToUserId") or None
    notes = data.get("notes")
    
    if not item_name or not category or not status:
        return failure("Item name, category, and status are required.", 400)
        
    try:
        run_query(
            """
            UPDATE inventory
            SET item_name = %s, category = %s, serial_number = %s, status = %s, assigned_to_user_id = %s, notes = %s
            WHERE id = %s
            """,
            (item_name, category, serial_number, status, assigned_to, notes, item_id)
        )
        return success({"message": "Inventory item updated successfully."})
    except Exception as e:
        if "Duplicate entry" in str(e) or "serial_number" in str(e):
            return failure("An item with this serial number already exists.", 400)
        return failure(str(e), 500)

@inventory_bp.route("/<int:item_id>", methods=["DELETE"])
@token_required
def delete_item(current_user_id, item_id):
    user = get_user(current_user_id)
    if not _is_authorized(user):
        return failure("Only Admin, Production, or Management can delete inventory.", 403)
        
    _ensure_table()
    run_query("DELETE FROM inventory WHERE id = %s", (item_id,))
    return success({"message": "Inventory item deleted successfully."})
