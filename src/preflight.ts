/**
 * Pre-flight checks for OpenEye.
 * Verifies Python and uvicorn are available before spawning the sidecar.
 */
import { execFile } from "node:child_process";

interface PreflightResult {
  ok: boolean;
  python: string | null;
  uvicorn: boolean;
  errors: string[];
}

function exec(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: 5000 }, (err, stdout) => {
      if (err) reject(err);
      else resolve(stdout.trim());
    });
  });
}

export async function checkPreflight(python?: string): Promise<PreflightResult> {
  const pythonCmd = python ?? process.env.OPENEYE_PYTHON ?? "python3";
  const errors: string[] = [];
  let pythonVersion: string | null = null;
  let uvicornOk = false;

  // Check Python
  try {
    pythonVersion = await exec(pythonCmd, ["--version"]);
  } catch {
    errors.push(`Python not found: ${pythonCmd}. Install Python 3.9+ or set OPENEYE_PYTHON.`);
  }

  // Check uvicorn
  if (pythonVersion) {
    try {
      await exec(pythonCmd, ["-c", "import uvicorn"]);
      uvicornOk = true;
    } catch {
      errors.push("uvicorn not installed. Run: pip install 'uvicorn[standard]'");
    }
  }

  return {
    ok: errors.length === 0,
    python: pythonVersion,
    uvicorn: uvicornOk,
    errors,
  };
}
