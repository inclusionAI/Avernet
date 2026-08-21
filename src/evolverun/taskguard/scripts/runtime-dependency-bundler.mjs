import { spawnSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  unlinkSync,
} from "node:fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";

function installedPackageDir(nodeModulesDir, packageName) {
  return join(nodeModulesDir, ...packageName.split("/"));
}

function isWithinDirectory(rootDir, candidatePath) {
  const relativePath = relative(rootDir, candidatePath);
  return relativePath === ""
    || (!isAbsolute(relativePath) && relativePath !== ".." && !relativePath.startsWith(`..${sep}`));
}

function resolutionContext(dependencyChain, searched, candidate) {
  const candidateContext = candidate ? `; candidate: ${candidate}` : "";
  return `dependency chain: ${dependencyChain.join(" -> ")}${candidateContext}; searched: ${searched.join(", ")}`;
}

function resolveInstalledPackageDir(rootDir, fromDir, packageName, dependencyChain) {
  const boundaryDir = resolve(rootDir);
  const searched = [];
  let currentDir = resolve(fromDir);

  if (!isWithinDirectory(boundaryDir, currentDir)) {
    throw new Error(
      `Invalid runtime dependency search origin "${currentDir}"; ${resolutionContext(dependencyChain, searched)}`,
    );
  }

  while (true) {
    if (basename(currentDir) !== "node_modules") {
      const packageDir = installedPackageDir(join(currentDir, "node_modules"), packageName);
      searched.push(packageDir);
      if (existsSync(packageDir)) {
        return { packageDir, searched };
      }
    }

    if (currentDir === boundaryDir) break;
    const parentDir = dirname(currentDir);
    if (parentDir === currentDir || !isWithinDirectory(boundaryDir, parentDir)) break;
    currentDir = parentDir;
  }

  throw new Error(
    `Missing runtime dependency "${packageName}"; ${resolutionContext(dependencyChain, searched)}`,
  );
}

function readManifest(sourceDir, packageDir, packageName, dependencyChain, searched) {
  const manifestPath = join(sourceDir, "package.json");
  if (!existsSync(manifestPath)) {
    throw new Error(
      `Missing package.json for runtime dependency "${packageName}" at ${manifestPath}; ${resolutionContext(dependencyChain, searched, packageDir)}`,
    );
  }

  try {
    return JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Invalid package.json for runtime dependency "${packageName}" at ${manifestPath}; ${resolutionContext(dependencyChain, searched, packageDir)}; ${message}`,
    );
  }
}

function validateTargetRelativePath(targetRelativePath, entryContext) {
  const { packageName, dependencyChain, searched, packageDir } = entryContext;
  const invalid = !targetRelativePath
    || targetRelativePath === "."
    || isAbsolute(targetRelativePath)
    || targetRelativePath === ".."
    || targetRelativePath.startsWith(`..${sep}`);

  if (invalid) {
    throw new Error(
      `Invalid runtime dependency target "${targetRelativePath}" for "${packageName}"; ${resolutionContext(dependencyChain, searched, packageDir)}`,
    );
  }
}

function collectRuntimeDependencyEntries(rootDir, rootDependencies) {
  const resolvedRootDir = resolve(rootDir);
  const nodeModulesDir = join(resolvedRootDir, "node_modules");
  const visitedSources = new Set();
  const visitedTargets = new Set();
  const manifestsBySource = new Map();
  const physicalEntries = [];
  const copyEntries = [];

  function visit(packageName, fromDir, parentChain) {
    const dependencyChain = [...parentChain, packageName];
    const { packageDir, searched } = resolveInstalledPackageDir(
      resolvedRootDir,
      fromDir,
      packageName,
      dependencyChain,
    );
    const sourceDir = realpathSync(packageDir);
    const targetRelativePath = relative(nodeModulesDir, packageDir);
    const entryContext = { packageName, dependencyChain, searched, packageDir };
    validateTargetRelativePath(targetRelativePath, entryContext);

    let manifest = manifestsBySource.get(sourceDir);
    if (!manifest) {
      manifest = readManifest(sourceDir, packageDir, packageName, dependencyChain, searched);
      manifestsBySource.set(sourceDir, manifest);
    }

    const entry = {
      packageName,
      sourceDir,
      targetRelativePath,
      ...entryContext,
    };
    if (!visitedSources.has(sourceDir)) {
      visitedSources.add(sourceDir);
      physicalEntries.push(entry);
    }

    if (visitedTargets.has(targetRelativePath)) return;
    visitedTargets.add(targetRelativePath);
    copyEntries.push(entry);

    const dependencies = Object.keys(manifest.dependencies ?? {}).sort();
    for (const dependencyName of dependencies) {
      visit(dependencyName, packageDir, dependencyChain);
    }
  }

  for (const packageName of rootDependencies) {
    visit(packageName, resolvedRootDir, []);
  }

  return { physicalEntries, copyEntries };
}

function rebuildTargetNodeModulesDir(targetNodeModulesDir) {
  try {
    if (lstatSync(targetNodeModulesDir).isSymbolicLink()) {
      unlinkSync(targetNodeModulesDir);
    } else {
      rmSync(targetNodeModulesDir, { recursive: true, force: true });
    }
  } catch (error) {
    if (!(error && typeof error === "object" && error.code === "ENOENT")) {
      throw error;
    }
  }
  mkdirSync(targetNodeModulesDir, { recursive: true });
}

export function collectRuntimeDependencyClosure(rootDir, rootDependencies) {
  return collectRuntimeDependencyEntries(rootDir, rootDependencies)
    .physicalEntries.map((entry) => entry.packageName);
}

export function bundleRuntimeDependencies({
  rootDir,
  packageDir,
  rootDependencies,
  log = () => {},
}) {
  const targetNodeModulesDir = resolve(packageDir, "node_modules");
  rebuildTargetNodeModulesDir(targetNodeModulesDir);
  const { physicalEntries, copyEntries } = collectRuntimeDependencyEntries(
    rootDir,
    rootDependencies,
  );

  for (const entry of copyEntries) {
    validateTargetRelativePath(entry.targetRelativePath, entry);
    const targetDir = resolve(targetNodeModulesDir, entry.targetRelativePath);
    if (!isWithinDirectory(targetNodeModulesDir, targetDir)) {
      throw new Error(
        `Invalid runtime dependency target "${entry.targetRelativePath}" for "${entry.packageName}"; ${resolutionContext(entry.dependencyChain, entry.searched, entry.packageDir)}`,
      );
    }

    mkdirSync(dirname(targetDir), { recursive: true });
    cpSync(entry.sourceDir, targetDir, {
      recursive: true,
      dereference: true,
      filter: (sourcePath) => {
        const sourceRelativePath = relative(entry.sourceDir, sourcePath);
        return sourceRelativePath !== "node_modules"
          && !sourceRelativePath.startsWith(`node_modules${sep}`);
      },
    });
    log(entry.packageName);
  }

  return physicalEntries.map((entry) => entry.packageName);
}

export function verifyRuntimeImports({ packageDir, moduleSpecifiers }) {
  const source = `for (const specifier of ${JSON.stringify(moduleSpecifiers)}) await import(specifier);`;
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "--eval", source],
    {
      cwd: packageDir,
      encoding: "utf8",
    },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    const diagnostics = (result.stderr || result.stdout || "unknown import failure").trim();
    throw new Error(`Runtime import verification failed in ${packageDir}:\n${diagnostics}`);
  }
}
