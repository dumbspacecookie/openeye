# Publishing OpenEye to npm

A checklist for the first publish of `@dumbspacecookie/openeye`. Once the
package is on the registry, future publishes can shrink to "bump version +
`npm publish`."

## Pre-publish (one time)

1. **Scope ownership.** `@dumbspacecookie` is my personal npm scope —
   every npm user gets one automatically with their username, so nothing
   to claim. The scoped name guarantees no squat collisions.

2. **Enable 2FA on the publishing npm account.** Hard requirement for
   security. `npm profile enable-2fa auth-and-writes`.

3. **Set up an npm access token** for CI publishes (not needed for the
   first manual publish, but useful later):
   `npm token create --read-only` (then a write-scoped one for releases).

## Pre-publish (every release)

1. **Bump the version** in `package.json` following semver. For alpha:
   `0.1.0` → `0.1.1` for patches, `0.2.0` for new features.

2. **Verify the build:**
   ```bash
   npm run typecheck
   npm test
   npm run build
   ```

3. **Run the publish dry-run** to see exactly what would be uploaded:
   ```bash
   npm pack --dry-run
   ```
   The output should contain:
   - `dist/` (compiled JS + .d.ts)
   - `sidecar/` (the Python FastAPI server — included in `files`)
   - `skills/` (the example skill markdowns)
   - `schemas/` (skill JSON schema)
   - `package.json`
   - `README.md`

   It should NOT contain:
   - `src/` (raw TypeScript)
   - `tests/`
   - `examples/`
   - `docs/`
   - `node_modules/`
   - any `.db` or `.log` files

   If something unexpected appears, fix `.npmignore` and re-run.

4. **Verify package size.** `npm pack` produces a tarball — check that
   it's well under 5MB. Currently expected: ~150KB compressed.

## First publish

```bash
# Login (interactive, prompts for 2FA)
npm login

# Publish public (default for unscoped) — scoped packages default to
# private, so we need --access public
npm publish --access public
```

The `prepublishOnly` script auto-runs typecheck, tests, and build before
the upload happens. If any fail, the publish aborts.

## Post-publish

```bash
# Verify
npm view @dumbspacecookie/openeye

# Install from a fresh directory to smoke-test
cd /tmp && mkdir openeye-smoke && cd openeye-smoke
npm init -y
npm install @dumbspacecookie/openeye
node -e "import('@dumbspacecookie/openeye').then(m => console.log(Object.keys(m)))"
```

Update the README's install instructions to use the npm path instead of
`git clone`.

## Unpublishing

If you publish something broken, you have 72 hours to fully unpublish:
```bash
npm unpublish @dumbspacecookie/openeye@<version>
```

After 72 hours npm only allows `deprecate`:
```bash
npm deprecate @dumbspacecookie/openeye@<version> "Broken — use 0.1.2 instead"
```

## Why dependencies are pinned with `~` instead of `^`

`pi-agent-core` and `pi-ai` are independent projects. A minor version
bump from them could break our integration (the agent loop expects
specific event shapes). `~0.66.0` allows patch updates but blocks the
0.67 bump until we test it ourselves.

When pi-agent-core is past 1.0 and stable, this can move to `^`.

## Releasing alpha to beta

Currently the README declares this is alpha. When promoting:

1. Bump to `0.2.0` (alpha) → `0.5.0` (beta) → `1.0.0` (stable).
2. Remove the "ALPHA SOFTWARE" banner from the top of README.
3. Add a CHANGELOG.md tracking what changed each version.
4. Tag the git commit: `git tag v1.0.0 && git push --tags`.
