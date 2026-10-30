export function renderPatch(container, changes) {
  for (const change of changes) {
    const heading = document.createElement('p');
    heading.textContent = `${change.facet} · ${change.path}\n이전 ${change.preimageHash}\n목표 ${change.targetHash}`;
    const patch = document.createElement('pre'); patch.textContent = change.patch;
    container.append(heading, patch);
  }
}
