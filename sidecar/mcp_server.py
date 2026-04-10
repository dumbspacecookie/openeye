"""
OpenEye MCP Server
Exposes OpenEye tools via the Model Context Protocol for use with
Claude Desktop, Cursor, Windsurf, or any MCP-compatible client.

Usage:
    python mcp_server.py

Configure in Claude Desktop:
    {
      "mcpServers": {
        "openeye": {
          "command": "python3",
          "args": ["/path/to/openeye/sidecar/mcp_server.py"]
        }
      }
    }
"""

import json
import os
import sys

# Ensure sidecar modules are importable
sys.path.insert(0, os.path.dirname(__file__))

from state import get_db
from skills import write_skill, recall_relevant_skills, build_skills_context


def _tool(name, description, input_schema, handler):
    return {"name": name, "description": description, "input_schema": input_schema, "handler": handler}


def handle_search_memory(args):
    db = get_db()
    results = db.search_messages(
        query=args["query"], tenant_id=args.get("tenant_id"), limit=args.get("limit", 20))
    return json.dumps(results, default=str)


def handle_search_frames(args):
    db = get_db()
    results = db.search_frames(
        query=args["query"], tenant_id=args.get("tenant_id"),
        procedure_id=args.get("procedure_id"), limit=args.get("limit", 20))
    return json.dumps(results, default=str)


def handle_recall_skill(args):
    skills = recall_relevant_skills(
        task_description=args["task"], domain=args.get("domain"), top_k=args.get("top_k", 5))
    return json.dumps(skills, default=str)


def handle_write_skill(args):
    result = write_skill(
        name=args["name"], content=args["content"],
        description=args.get("description"), domain=args.get("domain", "general"))
    return json.dumps(result, default=str)


def handle_start_visual_session(args):
    db = get_db()
    vsid = db.create_visual_session(
        device_type=args["device_type"], device_id=args.get("device_id"),
        procedure_id=args.get("procedure_id"), procedure_name=args.get("procedure_name"),
        user_id=args.get("user_id"), tenant_id=args.get("tenant_id"))
    return json.dumps({"visual_session_id": vsid})


def handle_end_visual_session(args):
    db = get_db()
    db.end_visual_session(args["visual_session_id"], outcome=args.get("outcome", "completed"))
    return json.dumps({"ok": True})


def handle_log_frame(args):
    db = get_db()
    fid = db.log_frame(
        visual_session_id=args["visual_session_id"],
        sequence_num=args["sequence_num"],
        scene_description=args["scene_description"],
        objects_detected=args.get("objects_detected"),
        step_context=args.get("step_context"),
        confidence=args.get("confidence"))
    return json.dumps({"frame_id": fid})


def handle_verify_step(args):
    db = get_db()
    vid = db.log_step_verification(
        visual_session_id=args["visual_session_id"],
        step_id=args["step_id"],
        result=args["result"],
        frame_id=args.get("frame_id"),
        step_name=args.get("step_name"),
        confidence=args.get("confidence"),
        reasoning=args.get("reasoning"))
    return json.dumps({"verification_id": vid})


TOOLS = [
    {
        "name": "search_memory",
        "description": "FTS5 search across all past agent sessions",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_frames",
        "description": "FTS5 search across all past frame descriptions",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "tenant_id": {"type": "string"},
                "procedure_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recall_skill",
        "description": "Retrieve relevant procedural skills for the current task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description to match skills against"},
                "domain": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["task"],
        },
    },
    {
        "name": "write_skill",
        "description": "Persist a new skill doc after completing a complex task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "domain": {"type": "string", "default": "general"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "start_visual_session",
        "description": "Begin a tracked AR/XR visual session",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_type": {"type": "string"},
                "device_id": {"type": "string"},
                "procedure_id": {"type": "string"},
                "procedure_name": {"type": "string"},
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
            },
            "required": ["device_type"],
        },
    },
    {
        "name": "end_visual_session",
        "description": "Close a visual session",
        "inputSchema": {
            "type": "object",
            "properties": {
                "visual_session_id": {"type": "string"},
                "outcome": {"type": "string", "default": "completed"},
            },
            "required": ["visual_session_id"],
        },
    },
    {
        "name": "log_frame",
        "description": "Record a frame's scene description",
        "inputSchema": {
            "type": "object",
            "properties": {
                "visual_session_id": {"type": "string"},
                "sequence_num": {"type": "integer"},
                "scene_description": {"type": "string"},
                "objects_detected": {"type": "array", "items": {"type": "string"}},
                "step_context": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["visual_session_id", "sequence_num", "scene_description"],
        },
    },
    {
        "name": "verify_step",
        "description": "Record a step verification result (pass, fail, uncertain) — the core RL reward signal",
        "inputSchema": {
            "type": "object",
            "properties": {
                "visual_session_id": {"type": "string"},
                "step_id": {"type": "string"},
                "result": {"type": "string", "enum": ["pass", "fail", "uncertain"]},
                "frame_id": {"type": "integer"},
                "step_name": {"type": "string"},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["visual_session_id", "step_id", "result"],
        },
    },
]

HANDLERS = {
    "search_memory": handle_search_memory,
    "search_frames": handle_search_frames,
    "recall_skill": handle_recall_skill,
    "write_skill": handle_write_skill,
    "start_visual_session": handle_start_visual_session,
    "end_visual_session": handle_end_visual_session,
    "log_frame": handle_log_frame,
    "verify_step": handle_verify_step,
}


def main():
    """Run as a stdio MCP server."""
    import logging
    logging.basicConfig(level=logging.INFO)

    # MCP stdio protocol: read JSON-RPC from stdin, write to stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "openeye", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            tool_name = msg.get("params", {}).get("name", "")
            arguments = msg.get("params", {}).get("arguments", {})
            handler = HANDLERS.get(tool_name)
            if handler:
                try:
                    result_text = handler(arguments)
                    response = {
                        "jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": result_text}]},
                    }
                except Exception as e:
                    response = {
                        "jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True},
                    }
            else:
                response = {
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
        elif method == "notifications/initialized":
            continue  # no response needed for notifications
        else:
            response = {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
