let cy;
let graphData;
let selectedNode = null;
let compareTargetNode = null;
let compareMode = false;
let expandedNeighborhood = false;
let panGuard = false;
let fitTimer = null;
let startupMode = true;
let startupFocusIds = [];
let visibleNodeCount = 0;
let nodeScaleFactor = 1;
let edgeOpacityFactor = 1;
let browserMode = "mapped";
let isFullMapMode = false;
let fullMapPreviousState = null;
let initialCenterDone = false;
let labelMode = "auto";
let labelRefreshTimer = null;

const STARTUP_NODE_LIMIT = 42;
const TOP_LABEL_LIMIT = 18;
const FULL_MAP_LABEL_LIMIT = 10;
const FULL_MAP_MORE_LABEL_LIMIT = 28;
const FULL_MAP_VIEWPORT_LABEL_LIMIT = 22;
const PLEX_PREFERRED_MIN = 12;
const FULL_MAP_MIN_DEGREE = 2;
const FULL_MAP_TARGET_ZOOM = 1.78;
const isCoarsePointer = window.matchMedia("(pointer: coarse)").matches;

const palette = [
  "#ff4d5e", "#d8b15d", "#72d6d1", "#9b7cff", "#56c271",
  "#f07a3f", "#d85fa6", "#8bd3ff", "#d7e36f", "#ffffff"
];

const controls = {
  cluster: document.querySelector("#clusterFilter"),
  weirdness: document.querySelector("#weirdnessFilter"),
  emotional: document.querySelector("#emotionalFilter"),
  johnny: document.querySelector("#johnnyFilter"),
  showPending: document.querySelector("#showPending"),
  reset: document.querySelector("#resetFilters"),
  focusSelected: document.querySelector("#focusSelected"),
  fitCluster: document.querySelector("#fitCluster"),
  showFullMap: document.querySelector("#showFullMap"),
  resetView: document.querySelector("#resetView"),
  resetViewOverlay: document.querySelector("#resetViewOverlay"),
  focusSelectedOverlay: document.querySelector("#focusSelectedOverlay"),
  showFullMapOverlay: document.querySelector("#showFullMapOverlay"),
  exitFullMap: document.querySelector("#exitFullMap"),
  labelMode: document.querySelector("#labelMode"),
  zoomIn: document.querySelector("#zoomIn"),
  zoomOut: document.querySelector("#zoomOut"),
  compareToggle: document.querySelector("#compareToggle"),
  graphMode: document.querySelector("#graphMode"),
  resultCount: document.querySelector("#resultCount"),
  browserSearch: document.querySelector("#movieBrowserSearch"),
  browserList: document.querySelector("#movieBrowserList"),
  mappedTab: document.querySelector("#mappedTab"),
  outliersTab: document.querySelector("#outliersTab"),
  askButton: document.querySelector("#graphAskButton"),
  askStatus: document.querySelector("#askStatus"),
  resetAsk: document.querySelector("#resetAsk"),
  entryPanel: document.querySelector("#graphEntryPanel"),
  graphPage: document.querySelector(".graph-page")
};

function colorFor(cluster) {
  let hash = 0;
  for (const char of cluster || "Outliers") hash = char.charCodeAt(0) + ((hash << 5) - hash);
  return palette[Math.abs(hash) % palette.length];
}

async function initGraph() {
  const params = new URLSearchParams(window.location.search);
  graphData = await fetch("/api/graph").then((res) => res.json());
  if (!graphData.nodes.length) {
    document.querySelector("#emptyGraph").hidden = false;
    document.querySelector("#cy").classList.add("is-empty");
  }
  const clusters = [...new Set(graphData.nodes.map((node) => node.data.cluster).filter(Boolean))].sort();
  clusters.forEach((cluster) => {
    const option = document.createElement("option");
    option.value = cluster;
    option.textContent = cluster;
    controls.cluster.append(option);
  });
  const initialCluster = params.get("cluster");
  if (initialCluster) controls.cluster.value = initialCluster;

  cy = cytoscape({
    container: document.getElementById("cy"),
    elements: [...graphData.nodes, ...graphData.edges],
    layout: { name: "preset" },
    minZoom: 0.62,
    maxZoom: 2.4,
    wheelSensitivity: 0.16,
    boxSelectionEnabled: false,
    autoungrabify: false,
    style: graphStyles()
  });

  cy.on("zoom pan", () => {
    keepGraphInView();
    scheduleLabelRefresh(60);
  });
  window.addEventListener("resize", () => scheduleFit(120));
  if (!isCoarsePointer) {
    cy.on("mouseover", "node", (event) => previewNeighborhood(event.target));
    cy.on("mouseout", "node", () => restoreHoverState());
  }
  cy.on("tap", "node", (event) => handleNodeTap(event.target));
  cy.on("tap", (event) => {
    if (event.target === cy) clearSelection();
  });

  document.querySelectorAll("#clusterFilter, #weirdnessFilter, #emotionalFilter, #johnnyFilter, #showPending")
    .forEach((el) => el.addEventListener("input", () => {
      applyFilters();
      if (el.id === "showPending" || el.id === "clusterFilter") runGraphLayout();
    }));
  controls.reset.addEventListener("click", resetFilters);
  controls.focusSelected.addEventListener("click", () => {
    if (selectedNode) focusNode(selectedNode, 1.18);
  });
  controls.focusSelectedOverlay.addEventListener("click", () => {
    if (selectedNode) focusNode(selectedNode, 1.18);
  });
  controls.fitCluster.addEventListener("click", fitSelectedCluster);
  controls.showFullMap.addEventListener("click", showFullMap);
  controls.showFullMapOverlay.addEventListener("click", showFullMap);
  controls.exitFullMap.addEventListener("click", () => exitFullMapMode());
  controls.resetView.addEventListener("click", resetView);
  controls.resetViewOverlay.addEventListener("click", resetView);
  controls.zoomIn.addEventListener("click", () => zoomBy(1.15));
  controls.zoomOut.addEventListener("click", () => zoomBy(0.87));
  controls.compareToggle.addEventListener("click", toggleCompareMode);
  controls.labelMode.addEventListener("input", () => {
    labelMode = controls.labelMode.value || "auto";
    refreshLabelSet();
  });
  controls.browserSearch.addEventListener("input", renderMovieBrowser);
  controls.mappedTab.addEventListener("click", () => setBrowserMode("mapped"));
  controls.outliersTab.addEventListener("click", () => setBrowserMode("outliers"));
  controls.focusSelected.disabled = true;
  controls.focusSelectedOverlay.disabled = true;
  controls.entryPanel.querySelectorAll("[data-entry-focus]").forEach((button) => {
    button.addEventListener("click", () => handleEntryFocus(button.dataset.entryFocus));
  });
  document.addEventListener("keydown", handleGlobalKeydown);
  updateFullMapControls();
  initGraphAsk();
  applyFilters();
  runGraphLayout();
  const initialTitleId = params.get("title_id");
  if (initialTitleId) {
    window.setTimeout(() => openTitleInMapById(initialTitleId, { forceSelect: true }), 720);
  }
}

function graphStyles() {
  return [
    {
      selector: "node",
      style: {
        label: (ele) => {
          const pending = ele.data("enrichment_status") !== "enriched";
          if (isCoarsePointer) {
            if (!ele.hasClass("selected-node") && !ele.hasClass("hovered") && !ele.hasClass("compare-node") && (!ele.hasClass("highlight") || startupMode)) return "";
            return ele.data("label");
          }
          if (pending && !ele.hasClass("hovered") && !ele.hasClass("selected-node") && !ele.hasClass("top-node") && !ele.hasClass("highlight")) return "";
          if (isFullMapMode) {
            const zoom = cy ? cy.zoom() : 1;
            const isImportant =
              ele.hasClass("selected-node")
              || ele.hasClass("hovered")
              || ele.hasClass("compare-node")
              || ele.hasClass("top-node")
              || ele.hasClass("highlight")
              || ele.hasClass("viewport-label");
            if (labelMode === "minimal") {
              if (!ele.hasClass("selected-node") && !ele.hasClass("hovered") && !ele.hasClass("compare-node")) return "";
            } else if (labelMode === "more") {
              if (!isImportant && zoom <= 1.02) return "";
            } else if (!isImportant && zoom <= 1.16) {
              return "";
            }
          }
          if (!ele.hasClass("hovered") && !ele.hasClass("selected-node") && !ele.hasClass("compare-node") && !ele.hasClass("top-node") && !ele.hasClass("highlight")) return "";
          return ele.data("label");
        },
        color: "#f4efe8",
        "font-size": (ele) => ele.data("enrichment_status") === "enriched" ? (isFullMapMode ? 13 : 11) : 9,
        "text-outline-color": isFullMapMode ? "rgba(7, 8, 13, 0.96)" : "#07080d",
        "text-outline-width": isFullMapMode ? 4 : 3,
        width: (ele) => {
          const score = Number(ele.data("johnny_core_score") || 1);
          const base = ele.data("enrichment_status") === "enriched" ? 22 + score * 5 : 12 + score * 2;
          const boost = isFullMapMode ? 1.15 : 1;
          return Math.max(ele.data("enrichment_status") === "enriched" ? 14 : 8, base * nodeScaleFactor * boost);
        },
        height: (ele) => {
          const score = Number(ele.data("johnny_core_score") || 1);
          const base = ele.data("enrichment_status") === "enriched" ? 22 + score * 5 : 12 + score * 2;
          const boost = isFullMapMode ? 1.15 : 1;
          return Math.max(ele.data("enrichment_status") === "enriched" ? 14 : 8, base * nodeScaleFactor * boost);
        },
        "background-color": (ele) => colorFor(ele.data("cluster")),
        "background-opacity": (ele) => ele.data("enrichment_status") === "enriched" ? 1 : 0.16,
        "border-color": "#ffffff",
        "border-opacity": (ele) => ele.data("enrichment_status") === "enriched" ? 0.4 : 0.16,
        "border-width": (ele) => ele.data("enrichment_status") === "enriched" ? 1.5 : 1,
        "border-style": (ele) => ele.data("enrichment_status") === "enriched" ? "solid" : "dashed",
        "shadow-blur": (ele) => ele.data("enrichment_status") === "enriched" ? (isFullMapMode ? 20 : 30) : 6,
        "shadow-color": (ele) => colorFor(ele.data("cluster")),
        "shadow-opacity": (ele) => ele.data("enrichment_status") === "enriched" ? (isFullMapMode ? 0.36 : 0.62) : 0.12,
        transition: "opacity 140ms, border-width 140ms, shadow-opacity 140ms"
      }
    },
    {
      selector: "edge",
      style: {
        width: (ele) => {
          if (ele.data("edge_type") === "bridge") return 1;
          if (ele.data("edge_type") === "soft") return 1;
          return 1 + Number(ele.data("weight") || 0.4) * 7;
        },
        "line-color": (ele) => {
          if (ele.data("edge_type") === "bridge") return "rgba(114, 214, 209, 0.28)";
          if (ele.data("edge_type") === "soft") return "rgba(169, 162, 160, 0.38)";
          return "rgba(244, 239, 232, 0.38)";
        },
        "line-style": (ele) => ele.data("edge_type") === "strong" ? "solid" : "dashed",
        "curve-style": "bezier",
        opacity: (ele) => {
          if (ele.data("edge_type") === "bridge") {
            return (isFullMapMode ? 0.08 : 0.16) * edgeOpacityFactor;
          }
          if (ele.data("edge_type") === "soft") {
            return (isFullMapMode ? 0.1 : 0.2) * edgeOpacityFactor;
          }
          return Math.min(isFullMapMode ? 0.28 : 0.7, ((isFullMapMode ? 0.05 : 0.12) + Number(ele.data("weight") || 0.4) * 0.32) * edgeOpacityFactor);
        }
      }
    },
    { selector: "node.hovered", style: { "border-width": 4, "border-opacity": 0.95, "shadow-opacity": 0.95 } },
    { selector: "node.selected-node", style: { "border-width": 5, "border-color": "#f4efe8", "shadow-blur": 48 } },
    { selector: "node.compare-node", style: { "border-width": 4, "border-color": "#72d6d1", "shadow-blur": 40, "shadow-opacity": 0.9 } },
    { selector: ".top-node", style: { "font-size": 11 } },
    { selector: ".startup-focus", style: { opacity: 1 } },
    { selector: "node.startup-muted", style: { opacity: 0.14, "shadow-opacity": 0.05, "border-opacity": 0.08 } },
    { selector: "edge.startup-muted", style: { opacity: 0.08 } },
    { selector: ".faded", style: { opacity: 0.1 } },
    { selector: ".highlight", style: { opacity: 1, "z-index": 10 } },
    { selector: ".filtered-out", style: { opacity: 0 } }
  ];
}

function applyFilters() {
  const cluster = controls.cluster.value;
  const weirdness = Number(controls.weirdness.value);
  const emotional = Number(controls.emotional.value);
  const johnny = Number(controls.johnny.value);
  const showPending = controls.showPending.checked;
  updateSliderLabels();
  let visible = 0;
  cy.nodes().forEach((node) => {
    const data = node.data();
    const enriched = data.enrichment_status === "enriched";
    const isOutlier = Boolean(Number(data.is_outlier || 0));
    const fullMapEligible = !isFullMapMode || isMeaningfulFullMapNode(node);
    const isVisible =
      (showPending || enriched) &&
      !isOutlier &&
      fullMapEligible &&
      (!cluster || data.cluster === cluster) &&
      Number(data.weirdness_score || 0) >= weirdness &&
      Number(data.emotional_weight_score || 0) >= emotional &&
      Number(data.johnny_core_score || 0) >= johnny;
    setElementVisible(node, isVisible);
    if (isVisible) visible += 1;
  });
  cy.edges().forEach((edge) => {
    setElementVisible(edge, edge.source().scratch("_visibleDesired") && edge.target().scratch("_visibleDesired"));
  });
  visibleNodeCount = visible;
  updateVisualDensity();
  controls.resultCount.textContent = `Showing ${visible} of ${cy.nodes().length} titles`;
  if (selectedNode && !selectedNode.scratch("_visibleDesired")) clearSelection();
  if (!selectedNode && !compareTargetNode && startupMode) {
    updateStartupFocus();
  } else {
    refreshLabelSet();
  }
  renderMovieBrowser();
  scheduleFit(180);
}

function setElementVisible(ele, visible) {
  ele.scratch("_visibleDesired", visible);
  ele.stop();
  if (visible) {
    ele.style("display", "element");
    ele.removeClass("filtered-out");
    return;
  }
  ele.addClass("filtered-out");
  window.setTimeout(() => {
    if (!ele.scratch("_visibleDesired")) ele.style("display", "none");
  }, 150);
}

function updateSliderLabels() {
  document.querySelector("#weirdnessValue").textContent = controls.weirdness.value;
  document.querySelector("#emotionalValue").textContent = controls.emotional.value;
  document.querySelector("#johnnyValue").textContent = controls.johnny.value;
}

function resetFilters() {
  controls.cluster.value = "";
  controls.weirdness.value = "1";
  controls.emotional.value = "1";
  controls.johnny.value = "1";
  controls.showPending.checked = false;
  setBrowserMode("mapped");
  startupMode = true;
  applyFilters();
  runGraphLayout();
}

function resetView() {
  updateFullMapControls();
  if (isFullMapMode) {
    exitFullMapMode();
    return;
  }
  compareMode = false;
  expandedNeighborhood = false;
  compareTargetNode = null;
  startupMode = true;
  controls.compareToggle.setAttribute("aria-pressed", "false");
  controls.compareToggle.classList.remove("is-active");
  clearSelection();
  applyFilters();
  fitMainCluster(true);
}

function runGraphLayout() {
  if (!cy.nodes().filter((node) => node.scratch("_visibleDesired")).length) return;
  const layout = cy.layout({
    name: "cose",
    animate: true,
    animationDuration: 650,
    animationEasing: "ease-out",
    fit: false,
    randomize: false,
    refresh: 24,
    nodeRepulsion: 6200,
    nodeOverlap: 10,
    idealEdgeLength: (edge) => edge.data("edge_type") === "soft"
      ? 130
      : 56 + (1 - Number(edge.data("weight") || 0.5)) * 70,
    edgeElasticity: (edge) => edge.data("edge_type") === "soft"
      ? 55
      : 150 + Number(edge.data("weight") || 0.5) * 240,
    nestingFactor: 0.9,
    gravity: 0.44,
    gravityRangeCompound: 1.4,
    gravityCompound: 1.0,
    numIter: 1800,
    initialTemp: 110,
    coolingFactor: 0.94,
    minTemp: 1,
    componentSpacing: 38
  });
  cy.one("layoutstop", () => {
    if (!initialCenterDone && !isFullMapMode) {
      initialCenterDone = true;
      window.setTimeout(() => {
        if (!selectedNode && !isFullMapMode) {
          fitMainCluster(true);
        }
      }, 300);
    }
    if (!selectedNode && startupMode) {
      updateStartupFocus();
      fitStartupFocus();
      setGraphModeNote("Start with one neighborhood. Pick a starting point, or click a title to pull its nearby taste cluster into focus.");
    } else {
      fitMainCluster();
    }
  });
  layout.run();
}

function fitVisibleGraph(maxZoomCap = 0.88) {
  const visible = cy.elements().filter((ele) => ele.visible() && !ele.hasClass("filtered-out"));
  if (!visible.length) return;
  fitElements(visible, 58, 0.66, maxZoomCap);
}

function fullMapElements() {
  const nodes = cy.nodes().filter((node) => node.visible() && isMeaningfulFullMapNode(node));
  const edges = cy.edges().filter((edge) => edge.visible() && nodes.contains(edge.source()) && nodes.contains(edge.target()));
  return nodes.union(edges);
}

function fitMainCluster(useAnimation = false) {
  if (selectedNode && selectedNode.visible()) {
    const neighborhood = selectedNode.closedNeighborhood().filter((ele) => ele.visible());
    fitElements(neighborhood, 88, 0.86, 1.04, useAnimation);
    return;
  }
  const core = mainStrongComponent();
  if (core && core.length) {
    const neighborhood = core.union(core.connectedEdges().filter((edge) => edge.data("edge_type") !== "soft" && edge.visible()));
    fitElements(neighborhood, 76, 0.7, 0.9, useAnimation);
    return;
  }
  fitVisibleGraph();
}

function fitStartupFocus() {
  const focusNodes = cy.collection(startupFocusIds.map((id) => cy.getElementById(id))).filter((node) => node.length && node.visible());
  if (!focusNodes.length) {
    fitMainCluster();
    return;
  }
  const focusEdges = cy.edges().filter((edge) => edge.visible() && focusNodes.contains(edge.source()) && focusNodes.contains(edge.target()));
  fitElements(focusNodes.union(focusEdges), 92, 0.68, 0.84, true);
}

function mainStrongComponent() {
  const strongEdges = cy.edges().filter((edge) => edge.visible() && edge.data("edge_type") !== "soft");
  const nodesWithStrongEdges = strongEdges.connectedNodes().filter((node) => node.visible());
  if (!nodesWithStrongEdges.length) return null;
  const visited = new Set();
  let bestIds = [];
  nodesWithStrongEdges.forEach((start) => {
    if (visited.has(start.id())) return;
    const stack = [start];
    const ids = [];
    visited.add(start.id());
    while (stack.length) {
      const node = stack.pop();
      ids.push(node.id());
      node.connectedEdges().filter((edge) => edge.visible() && edge.data("edge_type") !== "soft").forEach((edge) => {
        const other = edge.source().id() === node.id() ? edge.target() : edge.source();
        if (other.visible() && !visited.has(other.id())) {
          visited.add(other.id());
          stack.push(other);
        }
      });
    }
    if (ids.length > bestIds.length) bestIds = ids;
  });
  return cy.collection(bestIds.map((id) => cy.getElementById(id)));
}

function scheduleFit(delay = 120) {
  window.clearTimeout(fitTimer);
  fitTimer = window.setTimeout(() => {
    if (isFullMapMode) {
      fitFullMapElements();
      return;
    }
    if (selectedNode) {
      focusNode(selectedNode, 1.08);
      return;
    }
    if (startupMode && startupFocusIds.length) {
      fitStartupFocus();
      return;
    }
    fitVisibleGraph();
  }, delay);
}

function keepGraphInView() {
  if (panGuard || !cy) return;
  const visibleNodes = cy.nodes().filter((node) => node.visible());
  if (!visibleNodes.length) return;
  const box = visibleNodes.renderedBoundingBox({ includeLabels: false });
  const width = cy.width();
  const height = cy.height();
  const margin = 90;
  let pan = cy.pan();
  let nextX = pan.x;
  let nextY = pan.y;

  if (box.w < width - margin * 2) {
    nextX += (width / 2) - ((box.x1 + box.x2) / 2);
  } else {
    if (box.x2 < margin) nextX += margin - box.x2;
    if (box.x1 > width - margin) nextX -= box.x1 - (width - margin);
  }

  if (box.h < height - margin * 2) {
    nextY += (height / 2) - ((box.y1 + box.y2) / 2);
  } else {
    if (box.y2 < margin) nextY += margin - box.y2;
    if (box.y1 > height - margin) nextY -= box.y1 - (height - margin);
  }

  if (Math.abs(nextX - pan.x) > 1 || Math.abs(nextY - pan.y) > 1) {
    panGuard = true;
    cy.animate({ pan: { x: nextX, y: nextY } }, { duration: 120, complete: () => { panGuard = false; } });
  }
}

function scheduleLabelRefresh(delay = 80) {
  window.clearTimeout(labelRefreshTimer);
  labelRefreshTimer = window.setTimeout(() => refreshLabelSet(), delay);
}

function previewNeighborhood(node) {
  node.addClass("hovered");
  if (selectedNode) return;
  cy.elements().removeClass("highlight faded");
  cy.elements().addClass("faded");
  node.closedNeighborhood().removeClass("faded").addClass("highlight");
}

function restoreHoverState() {
  cy.nodes().removeClass("hovered");
  if (selectedNode || compareTargetNode) {
    applySelectionHighlight();
    return;
  }
  cy.elements().removeClass("highlight faded");
}

function clearSelection() {
  selectedNode = null;
  compareTargetNode = null;
  expandedNeighborhood = false;
  cy.elements().removeClass("highlight faded selected-node compare-node");
  if (startupMode) {
    updateStartupFocus();
  } else {
    cy.elements().removeClass("startup-focus startup-muted");
    refreshLabelSet();
  }
  renderMovieBrowser();
  loadSuggestedAsks();
  document.querySelector("#selectedAskActions").hidden = true;
  controls.focusSelected.disabled = true;
  controls.focusSelectedOverlay.disabled = true;
  setGraphModeNote(compareMode ? "Compare mode is on. Choose a title, then click another one to compare neighborhoods." : "Browse the map, then click a title to pull its neighborhood into focus.");
  document.querySelector("#details").innerHTML = `
    <p class="eyebrow">Taste context</p>
    <h2>Explore the map</h2>
    <p>Click a title for its neighborhood, taste signals, and nearby connections. Ask from here without leaving the map.</p>
    <div class="panel-hint">
      <strong>Default view</strong>
      <span>Mapped enriched titles are shown in the graph by default. Outliers stay browseable in the list, but they do not appear on the map.</span>
    </div>
    <div class="panel-hint">
      <strong>How to explore</strong>
      <span>Use the browser list when you know what you want. Use the graph when you want to wander by vibe, cluster, and connection strength.</span>
    </div>
  `;
}

function handleNodeTap(node) {
  if (compareMode && selectedNode && selectedNode.id() !== node.id()) {
    compareTargetNode = node;
    expandedNeighborhood = false;
    renderComparisonDetails(selectedNode, node);
    applySelectionHighlight();
    fitElements(selectedNode.closedNeighborhood().union(node.closedNeighborhood()), 82, 1.04);
    setGraphModeNote(`Comparing ${selectedNode.data("title")} with ${node.data("title")}.`);
    return;
  }
  showDetails(node);
}

function showDetails(node, options = {}) {
  const keepExpanded = options.keepExpanded || false;
  startupMode = false;
  controls.entryPanel.hidden = true;
  selectedNode = node;
  setBrowserMode(Number(node.data("is_outlier") || 0) ? "outliers" : "mapped");
  compareTargetNode = null;
  expandedNeighborhood = keepExpanded ? expandedNeighborhood : false;
  applySelectionHighlight();
  const data = node.data();
  const connectedEdges = node.connectedEdges().filter((edge) => edge.visible()).sort((a, b) => b.data("confidence") - a.data("confidence"));
  const strongMatches = renderConnectionList(node, connectedEdges.filter((edge) => edge.data("edge_type") !== "soft"));
  const softMatches = renderConnectionList(node, connectedEdges.filter((edge) => edge.data("edge_type") === "soft"));
  const placementNote = !strongMatches && !softMatches
    ? `<div class="panel-hint"><strong>Weakly placed</strong><span>This title needs more surrounding catalog context to place confidently.</span></div>`
    : "";
  const outlierNote = Number(data.is_outlier || 0)
    ? `<div class="panel-hint"><strong>Outlier</strong><span>This title needs more surrounding catalog context to place confidently.</span></div>`
    : "";
  document.querySelector("#details").innerHTML = `
    <p class="eyebrow">${data.cluster}</p>
    <h2>${data.title} ${data.year ? `<span>${data.year}</span>` : ""}</h2>
    <p><strong>${data.source}</strong> · ${data.enrichment_status === "enriched" ? "Enriched" : "Pending enrichment"}</p>
    ${outlierNote}
    ${placementNote}
    <p>${data.summary || "No summary available yet."}</p>
    <h3>Scores</h3>
    ${scoreCircles(data)}
    <h3>Top tags</h3>
    <div class="tag-row">${(data.tags || []).map((tag) => `<em>${tag}</em>`).join("") || "<em>Pending enrichment</em>"}</div>
    <h3>AI summary</h3>
    <p>${data.ai_summary || "Pending OpenAI enrichment."}</p>
    <div class="detail-actions">
      <button type="button" class="button" data-expand-neighborhood>${expandedNeighborhood ? "Collapse neighborhood" : "Expand this neighborhood"}</button>
      <button type="button" class="button" data-focus-title>Focus selected</button>
    </div>
    <h3>Strong matches</h3>
    <ul class="nearby-list">${strongMatches || "<li>No strong taste neighbors yet.</li>"}</ul>
    <h3>Soft matches</h3>
    ${!strongMatches && softMatches ? "<p>This title has no strong taste neighbors yet, but it loosely connects to...</p>" : ""}
    <ul class="nearby-list soft-list">${softMatches || "<li>No soft bridges yet.</li>"}</ul>
    <a class="button primary" href="/title/${data.id}">Open detail</a>
  `;
  attachDetailInteractions();
  renderMovieBrowser();
  loadSuggestedAsks(data.id);
  renderSelectedAskActions(data.title);
  controls.focusSelected.disabled = false;
  controls.focusSelectedOverlay.disabled = false;
  if (Number(data.is_outlier || 0)) {
    controls.focusSelected.disabled = true;
    controls.focusSelectedOverlay.disabled = true;
    cy.elements().removeClass("highlight faded selected-node compare-node");
    setGraphModeNote(`${data.title} is grouped with outliers. It stays in the browser and detail view, but does not render on the main map yet.`);
  } else {
    focusNode(node, 1.12);
    setGraphModeNote(compareMode ? `Compare mode is on. Click another title to compare it with ${data.title}.` : `Focused on ${data.title}. Explore its neighborhood or ask from this context.`);
  }
}

function renderMovieBrowser() {
  if (isFullMapMode) {
    controls.browserList.innerHTML = "<p class=\"muted-text\">Full map mode is focused on the graph. Exit full map to browse titles.</p>";
    return;
  }
  const query = controls.browserSearch.value.trim().toLowerCase();
  const nodes = cy.nodes()
    .filter((node) => {
      const enriched = node.data("enrichment_status") === "enriched";
      const isOutlier = Boolean(Number(node.data("is_outlier") || 0));
      if (!enriched) return false;
      if (browserMode === "outliers") return isOutlier;
      return node.scratch("_visibleDesired") && !isOutlier;
    })
    .sort((a, b) => a.data("title").localeCompare(b.data("title")));
  const filtered = nodes.filter((node) => {
    const data = node.data();
    return !query || `${data.title} ${data.year || ""} ${data.cluster || ""}`.toLowerCase().includes(query);
  });
  controls.browserList.innerHTML = filtered.map((node) => {
    const data = node.data();
    const selected = selectedNode && selectedNode.id() === node.id();
    return `
      <button type="button" class="movie-browser-item ${selected ? "selected" : ""}" data-node-id="${node.id()}">
        <span>
          <strong>${data.title}</strong>
          <small>${data.year || "n/a"} · ${data.cluster || "Outliers"}</small>
        </span>
        ${scoreCircles(data)}
      </button>
    `;
  }).join("") || `<p class="muted-text">${browserMode === "outliers" ? "No outliers in this view." : "No visible mapped titles."}</p>`;
  controls.browserList.querySelectorAll("[data-node-id]").forEach((button) => {
    button.addEventListener("click", () => {
      openTitleInMapById(button.dataset.nodeId);
    });
  });
}

function renderConnectionList(node, edges) {
  return edges.map((edge) => {
    const other = edge.source().id() === node.id() ? edge.target() : edge.source();
    const confidence = Number(edge.data("confidence") || edge.data("weight") || 0);
    const edgeType = edge.data("edge_type");
    const descriptor = edgeType === "bridge"
      ? " · bridge connection"
      : edgeType === "soft"
        ? " · loose bridge"
        : "";
    return `<li class="${edgeType !== "strong" ? "soft-match" : ""}"><button type="button" class="link-button" data-open-node="${other.id()}">${other.data("title")}</button> <span>${formatConfidence(confidence)}</span><small>${(edge.data("shared_traits") || []).slice(0, 3).join(" / ")}${descriptor}</small></li>`;
  }).join("");
}

function scoreCircles(dataOrScores) {
  const scores = dataOrScores.scores || {
    johnny_core: dataOrScores.johnny_core_score,
    weirdness: dataOrScores.weirdness_score,
    emotional_weight: dataOrScores.emotional_weight_score
  };
  return `
    <div class="score-circles">
      ${scoreCircle("johnny", "Johnny-core", scores.johnny_core)}
      ${scoreCircle("weird", "Weirdness", scores.weirdness)}
      ${scoreCircle("emotion", "Emotional weight", scores.emotional_weight)}
    </div>
  `;
}

function scoreCircle(kind, label, value) {
  const score = Number(value || 0);
  return `<span class="score-circle ${kind}" style="--score: ${score}" title="${label}: ${score || "Pending"}"><b>${score || "-"}</b></span>`;
}

function renderSelectedAskActions(title) {
  const panel = document.querySelector("#selectedAskActions");
  panel.hidden = false;
  panel.innerHTML = `
    <button type="button" data-question="What is similar to ${title}?">Similar to this</button>
    <button type="button" data-question="Give me weirder picks like ${title}.">Weirder picks</button>
    <button type="button" data-question="Give me emotionally heavier picks like ${title}.">Emotionally heavier</button>
    <button type="button" data-question="Why does ${title} connect to these?">Why it connects</button>
  `;
  panel.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => askTasteGraph(button.dataset.question));
  });
}

function initGraphAsk() {
  const form = document.querySelector("#graphAskForm");
  const input = document.querySelector("#graphAskQuestion");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    askTasteGraph(input.value.trim());
  });
  document.querySelector("#refreshSuggestions").addEventListener("click", () => {
    loadSuggestedAsks(selectedNode ? Number(selectedNode.id()) : null);
  });
  controls.resetAsk.addEventListener("click", resetAskPanel);
  loadSuggestedAsks();
  syncAskResetState();
}

async function loadSuggestedAsks(selectedTitleId = null) {
  const container = document.querySelector("#askPrompts");
  container.innerHTML = "<span class=\"muted-text\">Loading suggestions...</span>";
  const params = selectedTitleId ? `?selected_title_id=${encodeURIComponent(selectedTitleId)}` : "";
  const data = await fetch(`/api/suggested-asks${params}`).then((response) => response.json());
  container.innerHTML = "";
  (data.suggestions || []).forEach((suggestion) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = suggestion.label;
    button.dataset.question = suggestion.question;
    button.addEventListener("click", () => askTasteGraph(suggestion.question));
    container.append(button);
  });
}

async function askTasteGraph(question, explainWithAi = false) {
  if (!question) return;
  const input = document.querySelector("#graphAskQuestion");
  const answer = document.querySelector("#graphAskAnswer");
  const button = controls.askButton;
  const status = controls.askStatus;
  answer.hidden = false;
  input.value = question;
  syncAskResetState(true);
  button.disabled = true;
  button.classList.add("is-loading");
  button.textContent = "Thinking...";
  status.hidden = false;
  status.textContent = explainWithAi ? "Asking AI for a richer explanation..." : "Checking your taste graph...";
  answer.innerHTML = explainWithAi ? "<p>Asking AI for a richer explanation...</p>" : "<p>Checking the local graph...</p>";
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        explain_with_ai: explainWithAi,
        selected_title_id: selectedNode ? Number(selectedNode.id()) : null
      })
    });
    const data = await response.json();
    answer.innerHTML = renderAskAnswer(data);
    answer.querySelectorAll("[data-open-node]").forEach((buttonEl) => {
      buttonEl.addEventListener("click", () => openTitleInMapById(buttonEl.dataset.openNode, { forceSelect: true }));
    });
    const explainButton = answer.querySelector("[data-explain-ai]");
    if (explainButton) {
      explainButton.addEventListener("click", () => askTasteGraph(question, true));
    }
  } catch (error) {
    answer.innerHTML = "<div class=\"ask-answer-summary\"><h3>Couldn’t load recommendations</h3><p>Something interrupted the request. Try again in a moment.</p></div>";
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.textContent = "Ask";
    status.hidden = true;
    syncAskResetState();
  }
}

function renderAskAnswer(data) {
  const list = (items, emptyLabel = "No matches yet.") => (items || []).map((item) => {
    if (typeof item === "string") {
      return `<article class="ask-result-card empty"><p>${item}</p></article>`;
    }
    const scores = item.scores || {};
    const traits = (item.shared_traits || item.tags || []).slice(0, 4);
    const reason = item.reason || item.why || (item.edge_type === "soft" ? "Looser graph bridge." : "");
    const confidence = item.confidence != null ? `<span class="ask-result-confidence">${formatConfidence(item.confidence)}</span>` : "";
    return `
      <button type="button" class="ask-result-card ${item.edge_type === "soft" ? "soft" : ""}" ${item.id ? `data-open-node="${item.id}"` : ""}>
        <div class="ask-result-card-head">
          <div>
            <h5>${item.title}</h5>
            <p>${item.year ? `${item.year} · ` : ""}${item.cluster || "Outliers"}${item.edge_type === "bridge" ? " · bridge connection" : item.edge_type === "soft" ? " · looser match" : ""}</p>
          </div>
          ${confidence}
          ${scoreCircles({ scores })}
        </div>
        ${reason ? `<p class="ask-result-reason">${reason}</p>` : ""}
        <div class="ask-result-traits">
          ${traits.length ? traits.map((trait) => `<em>${trait}</em>`).join("") : "<span class=\"muted-text\">Shared traits still forming.</span>"}
        </div>
      </button>
    `;
  }).join("") || `<article class="ask-result-card empty"><p>${emptyLabel}</p></article>`;
  return `
    <div class="ask-answer-summary">
      <h3>${data.recommendation || "Taste Graph answer"}</h3>
      <p>${data.why_these_fit || data.why_it_fits || ""}</p>
    </div>
    ${data.answer_source === "local_graph" && data.can_explain_with_ai ? `<button class="ai-explain-button" type="button" data-explain-ai>Explain with AI</button>` : ""}
    <div class="ask-answer-grid">
      <section class="ask-result-group">
        <h4>Best matches</h4>
        <div class="ask-result-list">${list(data.best_matches || data.nearby_titles, "No strong recommendation set yet.")}</div>
      </section>
      <section class="ask-result-group">
        <h4>Weirder picks</h4>
        <div class="ask-result-list">${list(data.weirdest_matches, "No weird outliers yet.")}</div>
      </section>
      <section class="ask-result-group">
        <h4>Emotionally heavier</h4>
        <div class="ask-result-list">${list(data.emotionally_heavier_matches, "No heavier nearby matches yet.")}</div>
      </section>
      <section class="ask-result-group">
        <h4>Safer / easier</h4>
        <div class="ask-result-list">${list(data.safer_easier_watches, "No easier nearby picks yet.")}</div>
      </section>
      <section class="ask-result-group">
        <h4>Bridge titles</h4>
        <div class="ask-result-list">${list(data.bridge_titles, "No bridge titles surfaced yet.")}</div>
      </section>
    </div>
    <div class="tag-row">${(data.tags_driving_recommendation || data.tags_that_drove_answer || []).map((tag) => `<em>${tag}</em>`).join("")}</div>
  `;
}

function applySelectionHighlight() {
  cy.elements().removeClass("highlight faded selected-node compare-node startup-focus startup-muted");
  if (!selectedNode && !compareTargetNode) return;
  cy.elements().addClass("faded");
  let focusSet = cy.collection();
  if (selectedNode) {
    const primary = expandedNeighborhood ? selectedNode.closedNeighborhood().union(selectedNode.closedNeighborhood().closedNeighborhood()) : selectedNode.closedNeighborhood();
    selectedNode.addClass("selected-node");
    focusSet = focusSet.union(primary);
  }
  if (compareTargetNode) {
    compareTargetNode.addClass("compare-node");
    focusSet = focusSet.union(compareTargetNode.closedNeighborhood());
  }
  focusSet.removeClass("faded").addClass("highlight");
}

function focusNode(node, minZoom = 1.08) {
  if (!node || !node.length) return;
  cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), minZoom) }, { duration: 340, easing: "ease-out-cubic" });
}

function fitElements(elements, padding = 76, minZoom = 0.96, maxZoom = 1.12, animate = true) {
  if (!elements || !elements.length) return;
  const visible = elements.filter((ele) => ele.visible());
  const done = () => {
    if (cy.zoom() < minZoom) cy.zoom(minZoom);
    if (cy.zoom() > maxZoom) cy.zoom(maxZoom);
    keepGraphInView();
  };
  if (!animate) {
    cy.fit(visible, padding);
    done();
    return;
  }
  cy.animate({ fit: { eles: visible, padding } }, {
    duration: 380,
    easing: "ease-out-cubic",
    complete: done
  });
}

function fitSelectedCluster() {
  if (selectedNode) {
    const cluster = selectedNode.data("cluster");
    const nodes = cy.nodes().filter((node) => node.visible() && node.data("cluster") === cluster);
    const edges = cy.edges().filter((edge) => edge.visible() && nodes.contains(edge.source()) && nodes.contains(edge.target()));
    fitElements(nodes.union(edges), 88, 0.82, 0.96);
    return;
  }
  fitMainCluster();
}

function toggleCompareMode() {
  compareMode = !compareMode;
  controls.compareToggle.setAttribute("aria-pressed", String(compareMode));
  controls.compareToggle.classList.toggle("is-active", compareMode);
  if (!compareMode) {
    compareTargetNode = null;
    applySelectionHighlight();
    if (selectedNode) {
      showDetails(selectedNode);
    } else {
      setGraphModeNote("Browse the map, then click a title to pull its neighborhood into focus.");
    }
    return;
  }
  setGraphModeNote(selectedNode ? `Compare mode is on. Click another title to compare it with ${selectedNode.data("title")}.` : "Compare mode is on. Choose a title, then click another one to compare neighborhoods.");
}

function renderComparisonDetails(anchor, candidate) {
  const sharedTraits = sharedTraitIntersection(anchor.data("tags") || [], candidate.data("tags") || []);
  const scoreDiffs = [
    ["Johnny-core", anchor.data("johnny_core_score"), candidate.data("johnny_core_score")],
    ["Weirdness", anchor.data("weirdness_score"), candidate.data("weirdness_score")],
    ["Emotional weight", anchor.data("emotional_weight_score"), candidate.data("emotional_weight_score")]
  ];
  document.querySelector("#details").innerHTML = `
    <p class="eyebrow">Neighborhood compare</p>
    <h2>${anchor.data("title")} <span>vs ${candidate.data("title")}</span></h2>
    <p>Strong overlaps and score differences between two nearby titles in the map.</p>
    <div class="compare-grid">
      <div class="detail-panel">
        <strong>${anchor.data("title")}</strong>
        <p>${anchor.data("cluster") || "Outliers"} · ${anchor.data("year") || "n/a"}</p>
        ${scoreCircles(anchor.data())}
      </div>
      <div class="detail-panel">
        <strong>${candidate.data("title")}</strong>
        <p>${candidate.data("cluster") || "Outliers"} · ${candidate.data("year") || "n/a"}</p>
        ${scoreCircles(candidate.data())}
      </div>
    </div>
    <h3>Shared traits</h3>
    <div class="tag-row">${sharedTraits.length ? sharedTraits.map((trait) => `<em>${trait}</em>`).join("") : "<em>Thin overlap so far</em>"}</div>
    <h3>Score differences</h3>
    <ul class="compare-list">
      ${scoreDiffs.map(([label, left, right]) => `<li><strong>${label}</strong><span>${anchor.data("title")}: ${left || "-"} · ${candidate.data("title")}: ${right || "-"}</span></li>`).join("")}
    </ul>
    <div class="detail-actions">
      <button type="button" class="button" data-open-node="${candidate.id()}">Jump to ${candidate.data("title")}</button>
      <button type="button" class="button" data-stop-compare>Back to selected neighborhood</button>
    </div>
  `;
  attachDetailInteractions();
}

function sharedTraitIntersection(left, right) {
  const leftSet = new Set(left);
  return right.filter((item) => leftSet.has(item)).slice(0, 6);
}

function attachDetailInteractions() {
  document.querySelectorAll("#details [data-open-node]").forEach((button) => {
    button.addEventListener("click", () => openTitleInMapById(button.dataset.openNode, { forceSelect: true }));
  });
  const expandButton = document.querySelector("#details [data-expand-neighborhood]");
  if (expandButton) {
    expandButton.addEventListener("click", () => {
      expandedNeighborhood = !expandedNeighborhood;
      applySelectionHighlight();
      if (selectedNode) {
        focusNode(selectedNode, 1.06);
        showDetails(selectedNode, { keepExpanded: true });
      }
    });
  }
  const focusButton = document.querySelector("#details [data-focus-title]");
  if (focusButton) {
    focusButton.addEventListener("click", () => {
      if (selectedNode) focusNode(selectedNode, 1.16);
    });
  }
  const stopCompareButton = document.querySelector("#details [data-stop-compare]");
  if (stopCompareButton) {
    stopCompareButton.addEventListener("click", () => {
      compareTargetNode = null;
      applySelectionHighlight();
      if (selectedNode) showDetails(selectedNode);
    });
  }
}

function openTitleInMapById(nodeId, options = {}) {
  const node = cy.getElementById(String(nodeId));
  if (!node || !node.length) return;
  setBrowserMode(Number(node.data("is_outlier") || 0) ? "outliers" : "mapped");
  if (!Number(node.data("is_outlier") || 0)) {
    ensureNodeVisible(node);
  }
  if (!options.forceSelect && compareMode && selectedNode && selectedNode.id() !== node.id()) {
    handleNodeTap(node);
    return;
  }
  showDetails(node);
}

function isMeaningfulFullMapNode(node) {
  if (!node || !node.length) return false;
  if (node.data("enrichment_status") !== "enriched") return false;
  if (Number(node.data("is_outlier") || 0)) return false;
  if (!node.data("cluster")) return false;
  const strongDegree = node.connectedEdges().filter((edge) => edge.data("edge_type") !== "soft").length;
  return strongDegree >= FULL_MAP_MIN_DEGREE;
}

function ensureNodeVisible(node) {
  let changed = false;
  if (node.data("enrichment_status") !== "enriched" && !controls.showPending.checked) {
    controls.showPending.checked = true;
    changed = true;
  }
  if (controls.cluster.value && controls.cluster.value !== node.data("cluster")) {
    controls.cluster.value = "";
    changed = true;
  }
  const scoreTargets = [
    [controls.weirdness, Number(node.data("weirdness_score") || 1)],
    [controls.emotional, Number(node.data("emotional_weight_score") || 1)],
    [controls.johnny, Number(node.data("johnny_core_score") || 1)]
  ];
  scoreTargets.forEach(([control, value]) => {
    if (Number(control.value) > value) {
      control.value = String(Math.max(1, value));
      changed = true;
    }
  });
  if (changed) {
    applyFilters();
  }
}

function setGraphModeNote(message) {
  controls.graphMode.textContent = message;
}

function updateVisualDensity() {
  if (isFullMapMode) {
    nodeScaleFactor = visibleNodeCount > 260 ? 0.72 : 0.84;
    edgeOpacityFactor = visibleNodeCount > 260 ? 0.34 : 0.48;
  } else if (visibleNodeCount > 240) {
    nodeScaleFactor = 0.6;
    edgeOpacityFactor = 0.5;
  } else if (visibleNodeCount > 180) {
    nodeScaleFactor = 0.72;
    edgeOpacityFactor = 0.65;
  } else if (visibleNodeCount > 120) {
    nodeScaleFactor = 0.84;
    edgeOpacityFactor = 0.82;
  } else {
    nodeScaleFactor = 1;
    edgeOpacityFactor = 1;
  }
  cy.style().update();
}

function nodeImportance(node) {
  const strongWeight = node.connectedEdges().filter((edge) => edge.visible() && edge.data("edge_type") !== "soft")
    .reduce((sum, edge) => sum + Number(edge.data("confidence") || edge.data("weight") || 0), 0);
  const softWeight = node.connectedEdges().filter((edge) => edge.visible() && edge.data("edge_type") === "soft")
    .reduce((sum, edge) => sum + Number(edge.data("confidence") || edge.data("weight") || 0), 0);
  return strongWeight * 3 + softWeight + Number(node.data("johnny_core_score") || 0) * 0.25;
}

function refreshLabelSet() {
  cy.nodes().removeClass("top-node viewport-label");
  const labelLimit = isFullMapMode
    ? (labelMode === "more" ? FULL_MAP_MORE_LABEL_LIMIT : labelMode === "minimal" ? 6 : FULL_MAP_LABEL_LIMIT)
    : TOP_LABEL_LIMIT;
  const topNodes = cy.nodes()
    .filter((node) => node.visible() && node.data("enrichment_status") === "enriched")
    .sort((a, b) => nodeImportance(b) - nodeImportance(a))
    .slice(0, labelLimit);
  topNodes.forEach((node) => node.addClass("top-node"));
  if (isFullMapMode && labelMode !== "minimal") {
    const viewportNodes = cy.nodes()
      .filter((node) => node.visible() && node.data("enrichment_status") === "enriched")
      .sort((a, b) => viewportProximityScore(b) - viewportProximityScore(a))
      .slice(0, labelMode === "more" ? FULL_MAP_VIEWPORT_LABEL_LIMIT + 10 : FULL_MAP_VIEWPORT_LABEL_LIMIT);
    viewportNodes.forEach((node) => node.addClass("viewport-label"));
  }
}

function viewportProximityScore(node) {
  if (!cy || !node || !node.length) return 0;
  const pos = node.renderedPosition();
  const cx = cy.width() / 2;
  const cyCenter = cy.height() / 2;
  const dx = pos.x - cx;
  const dy = pos.y - cyCenter;
  const distance = Math.sqrt(dx * dx + dy * dy);
  const centerBias = Math.max(0, 1 - distance / Math.max(cx, cyCenter, 1));
  return centerBias * 100 + nodeImportance(node);
}

function updateStartupFocus() {
  const candidates = preferredStartingCandidates(
    cy.nodes().filter((node) => node.visible() && node.data("enrichment_status") === "enriched"),
    { requireEdges: true }
  )
    .sort((a, b) => nodeImportance(b) - nodeImportance(a));
  startupFocusIds = candidates.slice(0, STARTUP_NODE_LIMIT).map((node) => node.id());
  cy.elements().removeClass("startup-focus startup-muted highlight faded selected-node compare-node");
  refreshLabelSet();
  if (!startupFocusIds.length) {
    controls.entryPanel.hidden = true;
    return;
  }
  const focusNodes = cy.collection(startupFocusIds.map((id) => cy.getElementById(id)));
  const focusEdges = cy.edges().filter((edge) => edge.visible() && focusNodes.contains(edge.source()) && focusNodes.contains(edge.target()));
  cy.elements().addClass("startup-muted");
  focusNodes.removeClass("startup-muted").addClass("startup-focus highlight");
  focusEdges.removeClass("startup-muted").addClass("startup-focus");
  controls.entryPanel.hidden = false;
}

function parseRecencyValue(node) {
  return node.data("added_at") || node.data("updated_at") || node.data("created_at") || `${String(node.id()).padStart(12, "0")}`;
}

function handleEntryFocus(mode) {
  const candidates = preferredStartingCandidates(
    cy.nodes().filter((node) => node.visible() && node.data("enrichment_status") === "enriched"),
    { requireEdges: true }
  );
  if (!candidates.length) return;
  if (mode === "full") {
    showFullMap();
    return;
  }
  let target = null;
  if (mode === "top_johnny") {
    target = candidates.sort((a, b) =>
      comparePreferredNodes(
        a,
        b,
        ["johnny_core_score", "edge_count", "weirdness_score", "emotional_weight_score"]
      )
    )[0];
  } else if (mode === "most_weird") {
    target = candidates.sort((a, b) =>
      comparePreferredNodes(
        a,
        b,
        ["weirdness_score", "edge_count", "johnny_core_score", "updated_at"]
      )
    )[0];
  } else if (mode === "recent") {
    target = candidates.sort((a, b) => comparePreferredNodes(a, b, ["updated_at", "edge_count"]))[0];
  } else if (mode === "random") {
    target = candidates[Math.floor(Math.random() * candidates.length)];
  }
  if (target) showDetails(target);
}

function showFullMap() {
  if (isFullMapMode) {
    fitFullMapElements();
    return;
  }
  fullMapPreviousState = {
    zoom: cy.zoom(),
    pan: cy.pan(),
    startupMode,
  };
  isFullMapMode = true;
  startupMode = false;
  controls.entryPanel.hidden = true;
  controls.graphPage.classList.add("is-full-map-mode");
  updateFullMapControls();
  cy.elements().removeClass("startup-focus startup-muted highlight faded");
  applyFilters();
  refreshLabelSet();
  fitFullMapElements(true);
  setGraphModeNote("Full map mode: focused on the most connected neighborhoods. Exit full map to return to the full workspace.");
}

function fitFullMapElements(animate = true) {
  const elements = fullMapElements();
  if (!elements.length) {
    fitVisibleGraph(0.92);
    return;
  }
  const nodes = elements.nodes().filter((node) => node.visible());
  if (!nodes.length) return;
  const targetZoom = Math.min(cy.maxZoom(), Math.max(1.42, FULL_MAP_TARGET_ZOOM));
  if (!animate) {
    cy.center(nodes);
    cy.zoom(targetZoom);
    keepGraphInView();
    refreshLabelSet();
    return;
  }
  cy.animate(
    {
      center: { eles: nodes },
      zoom: targetZoom
    },
    {
      duration: 420,
      easing: "ease-out-cubic",
      complete: () => {
        keepGraphInView();
        refreshLabelSet();
      }
    }
  );
}

function exitFullMapMode() {
  if (!isFullMapMode) return;
  isFullMapMode = false;
  controls.graphPage.classList.remove("is-full-map-mode");
  updateFullMapControls();
  fullMapPreviousState = null;
  startupMode = true;
  applyFilters();
  refreshLabelSet();
  fitMainCluster(true);
  renderMovieBrowser();
  setGraphModeNote(selectedNode ? `Focused on ${selectedNode.data("title")}. Explore its neighborhood or ask from this context.` : "Browse the map, then click a title to pull its neighborhood into focus.");
}

function updateFullMapControls() {
  const showExit = Boolean(isFullMapMode);
  const showEnter = !showExit;

  controls.showFullMap.hidden = !showEnter;
  controls.showFullMap.classList.toggle("is-hidden", !showEnter);
  controls.showFullMap.setAttribute("aria-hidden", String(!showEnter));

  controls.showFullMapOverlay.hidden = !showEnter;
  controls.showFullMapOverlay.classList.toggle("is-hidden", !showEnter);
  controls.showFullMapOverlay.setAttribute("aria-hidden", String(!showEnter));

  controls.exitFullMap.hidden = !showExit;
  controls.exitFullMap.classList.toggle("is-hidden", !showExit);
  controls.exitFullMap.setAttribute("aria-hidden", String(!showExit));

  const labelControl = controls.labelMode.closest(".label-mode-control");
  if (labelControl) {
    labelControl.classList.toggle("is-hidden", !showExit);
    labelControl.setAttribute("aria-hidden", String(!showExit));
  }
}

function zoomBy(factor) {
  const targetZoom = Math.max(cy.minZoom(), Math.min(cy.maxZoom(), cy.zoom() * factor));
  cy.animate({ zoom: targetZoom }, { duration: 180 });
}

function preferredStartingCandidates(nodes, options = {}) {
  const list = Array.from(nodes);
  const mapped = list.filter((node) => !Number(node.data("is_outlier") || 0));
  const basePool = mapped.length >= PLEX_PREFERRED_MIN ? mapped : list;
  const withEdges = options.requireEdges
    ? basePool.filter((node) => node.connectedEdges().filter((edge) => edge.visible()).length > 0)
    : basePool;
  const plex = withEdges.filter((node) => node.data("source") === "plex");
  if (plex.length >= PLEX_PREFERRED_MIN) return plex;
  return withEdges;
}

function setBrowserMode(mode) {
  browserMode = mode === "outliers" ? "outliers" : "mapped";
  controls.mappedTab.classList.toggle("is-active", browserMode === "mapped");
  controls.outliersTab.classList.toggle("is-active", browserMode === "outliers");
  controls.mappedTab.setAttribute("aria-pressed", String(browserMode === "mapped"));
  controls.outliersTab.setAttribute("aria-pressed", String(browserMode === "outliers"));
  renderMovieBrowser();
}

function comparePreferredNodes(left, right, keys = []) {
  const sourceCompare = sourcePriority(left) - sourcePriority(right);
  if (sourceCompare !== 0) return sourceCompare;
  for (const key of keys) {
    const leftValue = sortableNodeValue(left, key);
    const rightValue = sortableNodeValue(right, key);
    if (leftValue > rightValue) return -1;
    if (leftValue < rightValue) return 1;
  }
  return String(left.data("title") || "").localeCompare(String(right.data("title") || ""));
}

function sourcePriority(node) {
  return node.data("source") === "plex" ? 0 : 1;
}

function sortableNodeValue(node, key) {
  if (key === "edge_count") {
    return node.connectedEdges().filter((edge) => edge.visible()).length;
  }
  if (key === "updated_at") {
    return String(parseRecencyValue(node));
  }
  return Number(node.data(key) || 0);
}

function formatConfidence(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape" && isFullMapMode) {
    exitFullMapMode();
  }
}

function resetAskPanel() {
  const input = document.querySelector("#graphAskQuestion");
  const answer = document.querySelector("#graphAskAnswer");
  input.value = "";
  answer.hidden = true;
  answer.innerHTML = "";
  controls.askStatus.hidden = true;
  controls.askStatus.textContent = "Checking your taste graph...";
  controls.askButton.disabled = false;
  controls.askButton.classList.remove("is-loading");
  controls.askButton.textContent = "Ask";
  loadSuggestedAsks(selectedNode ? Number(selectedNode.id()) : null);
  syncAskResetState();
}

function syncAskResetState(forceEnabled = false) {
  const input = document.querySelector("#graphAskQuestion");
  const answer = document.querySelector("#graphAskAnswer");
  const hasAnswer = !answer.hidden && answer.innerHTML.trim().length > 0;
  const hasQuestion = input.value.trim().length > 0;
  controls.resetAsk.disabled = !(forceEnabled || hasAnswer || hasQuestion);
}

initGraph();
