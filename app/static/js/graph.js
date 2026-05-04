let cy;
let graphData;
let graphDataLoaded = false;
let graphReady = false;
let layoutReady = false;
let graphRenderMode = "normal";
let graphBootIntent = null;
let bootGeneration = 0;
let userInteractionGeneration = 0;
let userCameraGeneration = 0;
let initialCameraApplied = false;
let cameraBusy = false;
let cameraFocusElements = null;
let pendingFocusRequest = null;
let pendingCameraAction = null;
let cameraFlushTimer = null;
let graphGeneration = 0;
let filterGeneration = 0;
let cameraGeneration = 0;
let selectionGeneration = 0;
let userHasSelectedTitleInCurrentFilter = false;
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
let selectedTitleId = null;
let selectedOutlierTitleId = null;
let previewTitleId = null;
let previewNode = null;
let previewFocusElements = null;
let previewMutedElements = null;
let listPreviewTimer = null;
let browserScrollTimer = null;
let nodeLookup = new Map();
let neighborLookup = new Map();
let browserRowLookup = new Map();
let hoverPreviewLocked = false;
let suggestedAskRequestId = 0;
let askRequestId = 0;
let askExplainRequestId = 0;
let activeAskState = null;
let currentLayout = null;
let layoutRunGeneration = 0;
let temporaryOutlierNodeId = null;
let temporaryOutlierEdgeIds = [];
let outlierPreviewHiddenNodeIds = [];
let outlierPreviewHiddenEdgeIds = [];
let graphLoadingTimeout = null;

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

const CAMERA_PRIORITIES = {
  initial_cluster_fit: 1,
  fit_cluster: 2,
  reset_view: 3,
  full_map: 4,
  outlier_preview: 5,
  selected_title_focus: 5,
};

const controls = {
  cluster: document.querySelector("#clusterFilter"),
  weirdness: document.querySelector("#weirdnessFilter"),
  emotional: document.querySelector("#emotionalFilter"),
  johnny: document.querySelector("#johnnyFilter"),
  showPending: document.querySelector("#showPending"),
  pendingToggleRow: document.querySelector(".toggle-row"),
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
  browserHelper: document.querySelector("#browserHelper"),
  allMappedTab: document.querySelector("#allMappedTab"),
  recentTab: document.querySelector("#recentTab"),
  johnnyTab: document.querySelector("#johnnyTab"),
  weirdTab: document.querySelector("#weirdTab"),
  emotionTab: document.querySelector("#emotionTab"),
  askButton: document.querySelector("#graphAskButton"),
  askStatus: document.querySelector("#askStatus"),
  resetAsk: document.querySelector("#resetAsk"),
  entryPanel: document.querySelector("#graphEntryPanel"),
  graphPage: document.querySelector(".graph-page")
};

function showGraphLoading(title = "Loading taste map…", copy = "Building the visible graph neighborhood.") {
  const shell = document.querySelector("#graphLoadingState");
  if (!shell) return;
  const titleEl = shell.querySelector(".graph-loading-title");
  const copyEl = shell.querySelector(".graph-loading-copy");
  if (titleEl) titleEl.textContent = title;
  if (copyEl) copyEl.textContent = copy;
  shell.classList.remove("is-hidden");
}

function hideGraphLoading(reason = "ready") {
  const shell = document.querySelector("#graphLoadingState");
  if (!shell) return;
  window.clearTimeout(graphLoadingTimeout);
  graphLoadingTimeout = null;
  shell.classList.add("is-hidden");
  console.debug("graph loading hidden", { reason, timestamp: cameraTimestamp() });
}

function showGraphError(message = "Couldn’t load the taste map.", detail = "Refresh or check the server logs.") {
  const shell = document.querySelector("#graphLoadingState");
  if (!shell) return;
  const titleEl = shell.querySelector(".graph-loading-title");
  const copyEl = shell.querySelector(".graph-loading-copy");
  if (titleEl) titleEl.textContent = message;
  if (copyEl) copyEl.textContent = detail;
  shell.classList.remove("is-hidden");
}

function setGraphVisualMode(mode = "default") {
  if (!controls.graphPage) return;
  controls.graphPage.classList.remove("graph-mode-selected", "graph-mode-outlier-preview", "dimmed-background");
  if (mode === "selected") {
    controls.graphPage.classList.add("graph-mode-selected");
  }
}

function cameraTimestamp() {
  return new Date().toISOString();
}

function logCameraEvent(kind, payload = {}) {
  console.debug(`CAMERA ${kind}`, {
    timestamp: cameraTimestamp(),
    ...payload,
  });
}

function cancelPendingCamera(reason = "manual-cancel") {
  if (pendingCameraAction) {
    logCameraEvent("CANCEL", {
      mode: pendingCameraAction.mode,
      reason,
      priority: pendingCameraAction.priority,
      generation: pendingCameraAction.cameraGeneration,
      userCameraGeneration,
      userInteractionGeneration,
      selectedTitleId,
      userHasSelectedTitle: userHasSelectedTitleInCurrentFilter,
      graphDataLoaded,
      layoutReady,
    });
  }
  pendingCameraAction = null;
  window.clearTimeout(cameraFlushTimer);
  cameraFlushTimer = null;
}

function cancelGraphMotion(reason = "manual-stop") {
  if (!cy) return;
  console.debug("graph motion cancel", {
    timestamp: cameraTimestamp(),
    reason,
    selectedTitleId,
    selectedOutlierTitleId,
    userCameraGeneration,
    userInteractionGeneration
  });
  try {
    cy.stop();
    cy.nodes().stop();
    cy.edges().stop();
  } catch (error) {
    console.debug("graph motion cancel failed", { reason, error: String(error) });
  }
  panGuard = false;
  window.clearTimeout(fitTimer);
}

function restoreElementDisplayState() {
  if (!cy) return;
  cy.nodes().forEach((node) => {
    const desired = Boolean(node.scratch("_visibleDesired"));
    if (desired) {
      node.style("display", "element");
      node.removeClass("filtered-out");
      return;
    }
    if (temporaryOutlierNodeId && node.id() === temporaryOutlierNodeId) {
      node.style("display", "element");
      return;
    }
    node.style("display", "none");
  });
  cy.edges().forEach((edge) => {
    const shouldShow = edge.source().style("display") !== "none" && edge.target().style("display") !== "none";
    edge.style("display", shouldShow ? "element" : "none");
  });
}

function setOutlierPreviewRenderMode(outlierNode, candidateNodes, tempEdges) {
  graphRenderMode = "outlier_preview";
  const focusNodes = outlierNode.union(candidateNodes);
  const focusNodeIds = new Set(focusNodes.map((node) => node.id()));
  const focusEdgeIds = new Set(tempEdges.map((edge) => edge.id()));
  outlierPreviewHiddenNodeIds = [];
  outlierPreviewHiddenEdgeIds = [];

  cy.nodes().forEach((node) => {
    if (focusNodeIds.has(node.id())) {
      node.style("display", "element");
      node.removeClass("filtered-out");
      return;
    }
    if (node.style("display") !== "none") {
      outlierPreviewHiddenNodeIds.push(node.id());
    }
    node.style("display", "none");
  });

  cy.edges().forEach((edge) => {
    if (focusEdgeIds.has(edge.id())) {
      edge.style("display", "element");
      return;
    }
    if (edge.style("display") !== "none") {
      outlierPreviewHiddenEdgeIds.push(edge.id());
    }
    edge.style("display", "none");
  });
}

function colorFor(cluster) {
  let hash = 0;
  for (const char of cluster || "Outliers") hash = char.charCodeAt(0) + ((hash << 5) - hash);
  return palette[Math.abs(hash) % palette.length];
}

async function initGraph() {
  console.time("graph init total");
  showGraphLoading();
  window.clearTimeout(graphLoadingTimeout);
  graphLoadingTimeout = window.setTimeout(() => {
    console.warn("graph loading timeout reached");
    hideGraphLoading("timeout");
  }, 10000);
  const params = new URLSearchParams(window.location.search);
  graphBootIntent = parseGraphBootIntent(params);
  bootGeneration += 1;
  console.debug("graph load start", {
    timestamp: cameraTimestamp(),
    cluster: params.get("cluster"),
    titleId: params.get("title_id"),
    shortcut: params.get("shortcut"),
    bootIntent: graphBootIntent,
    bootGeneration
  });
  console.time("graph fetch");
  try {
    graphData = await fetch("/api/graph").then((res) => {
      if (!res.ok) throw new Error(`graph-fetch-${res.status}`);
      return res.json();
    });
  } catch (error) {
    console.error("graph fetch failed", error);
    showGraphError();
    console.timeEnd("graph init total");
    return;
  }
  console.timeEnd("graph fetch");
  graphDataLoaded = true;
  graphGeneration += 1;
  const pendingCount = graphData.nodes.filter((node) => node.data.enrichment_status !== "enriched").length;
  if (controls.pendingToggleRow && pendingCount === 0) {
    controls.pendingToggleRow.hidden = true;
  }
  if (!graphData.nodes.length) {
    document.querySelector("#emptyGraph").hidden = false;
    document.querySelector("#cy").classList.add("is-empty");
    hideGraphLoading("empty-graph");
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
  if (initialCluster) filterGeneration += 1;

  console.time("graph cytoscape init");
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
  buildGraphCaches();
  console.timeEnd("graph cytoscape init");

  cy.on("zoom pan", () => {
    scheduleLabelRefresh(60);
  });
  window.addEventListener("resize", () => {
    if (isFullMapMode) {
      requestCamera({ mode: "full_map", reason: "resize" });
      return;
    }
    if (selectedTitleId) {
      requestCamera({ mode: "selected_title_focus", titleId: selectedTitleId, reason: "resize" });
      return;
    }
    if (controls.cluster.value) {
      requestCamera({ mode: "fit_cluster", cluster: controls.cluster.value, reason: "resize" });
      return;
    }
    requestCamera({ mode: "reset_view", reason: "resize" });
  });
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
      clearTemporaryOutlierPreview();
      selectedOutlierTitleId = null;
      applyFilters();
      if (el.id === "showPending" || el.id === "clusterFilter") {
        filterGeneration += 1;
        userHasSelectedTitleInCurrentFilter = false;
        userInteractionGeneration += 1;
        if (el.id === "clusterFilter") {
          selectedTitleId = null;
          selectedNode = null;
        }
        initialCameraApplied = false;
        cancelPendingCamera(`filter-change-${el.id}`);
        requestCamera({ mode: controls.cluster.value ? "fit_cluster" : "reset_view", cluster: controls.cluster.value, reason: `filter-${el.id}` });
        runGraphLayout();
      }
    }));
  controls.reset.addEventListener("click", resetFilters);
  controls.focusSelected?.addEventListener("click", () => {
    if (selectedTitleId) focusTitleOnGraph(selectedTitleId, { forceSelect: true, smooth: true, reason: "focus-button" });
  });
  controls.focusSelectedOverlay?.addEventListener("click", () => {
    if (selectedTitleId) focusTitleOnGraph(selectedTitleId, { forceSelect: true, smooth: true, reason: "focus-button-overlay" });
  });
  controls.fitCluster?.addEventListener("click", fitSelectedCluster);
  controls.showFullMap?.addEventListener("click", showFullMap);
  controls.showFullMapOverlay?.addEventListener("click", showFullMap);
  controls.exitFullMap.addEventListener("click", () => exitFullMapMode());
  controls.resetView?.addEventListener("click", resetView);
  controls.resetViewOverlay?.addEventListener("click", resetView);
  controls.zoomIn.addEventListener("click", () => zoomBy(1.15));
  controls.zoomOut.addEventListener("click", () => zoomBy(0.87));
  controls.compareToggle?.addEventListener("click", toggleCompareMode);
  controls.labelMode.addEventListener("input", () => {
    labelMode = controls.labelMode.value || "auto";
    refreshLabelSet();
  });
  controls.browserSearch.addEventListener("input", renderMovieBrowser);
  controls.browserList.addEventListener("scroll", handleBrowserScroll, { passive: true });
  controls.allMappedTab?.addEventListener("click", () => setBrowserMode("mapped"));
  controls.recentTab?.addEventListener("click", () => setBrowserMode("recent"));
  controls.johnnyTab?.addEventListener("click", () => setBrowserMode("johnny"));
  controls.weirdTab?.addEventListener("click", () => setBrowserMode("weird"));
  controls.emotionTab?.addEventListener("click", () => setBrowserMode("emotion"));
  if (controls.focusSelected) controls.focusSelected.disabled = true;
  if (controls.focusSelectedOverlay) controls.focusSelectedOverlay.disabled = true;
  controls.entryPanel.querySelectorAll("[data-entry-focus]").forEach((button) => {
    button.addEventListener("click", () => handleEntryFocus(button.dataset.entryFocus));
  });
  document.addEventListener("keydown", handleGlobalKeydown);
  updateFullMapControls();
  initGraphAsk();
  console.time("apply filters");
  applyFilters();
  console.timeEnd("apply filters");
  applyInitialGraphIntent(graphBootIntent);
  window.requestAnimationFrame(() => {
    runGraphLayout();
  });
  console.debug("graph load end", {
    timestamp: cameraTimestamp(),
    totalNodes: graphData.nodes.length,
    totalEdges: graphData.edges.length,
    graphGeneration,
    bootIntent: graphBootIntent,
    bootGeneration
  });
  console.timeEnd("graph init total");
}

function parseGraphBootIntent(params) {
  const titleId = params.get("title_id");
  const cluster = params.get("cluster");
  const shortcut = params.get("shortcut");
  if (titleId) {
    return {
      mode: "selected_title",
      titleId: String(titleId),
      cluster: cluster || "",
      shortcut: shortcut || "",
      source: "query-title"
    };
  }
  if (shortcut) {
    return {
      mode: "shortcut",
      titleId: "",
      cluster: cluster || "",
      shortcut,
      source: "query-shortcut"
    };
  }
  if (cluster) {
    return {
      mode: "cluster",
      titleId: "",
      cluster,
      shortcut: "",
      source: "query-cluster"
    };
  }
  return {
    mode: "default",
    titleId: "",
    cluster: "",
    shortcut: "",
    source: "default"
  };
}

function applyInitialGraphIntent(intent) {
  console.debug("graph boot intent apply", {
    timestamp: cameraTimestamp(),
    bootIntent: intent,
    bootGeneration,
    graphDataLoaded,
    layoutReady,
    graphReady
  });
  cancelPendingCamera("boot-intent");
  cancelGraphMotion("boot-intent");
  if (!intent || intent.mode === "default") {
    startupMode = true;
    requestCamera({ mode: "reset_view", reason: "boot-default", bootGeneration });
    return;
  }
  if (intent.mode === "cluster") {
    startupMode = false;
    userHasSelectedTitleInCurrentFilter = false;
    requestCamera({ mode: "initial_cluster_fit", cluster: intent.cluster || controls.cluster.value, reason: "boot-cluster", bootGeneration });
    return;
  }
  if (intent.mode === "selected_title") {
    startupMode = false;
    userHasSelectedTitleInCurrentFilter = true;
    userInteractionGeneration += 1;
    userCameraGeneration += 1;
    pendingFocusRequest = {
      titleId: intent.titleId,
      reason: "boot-selected-title",
      userCameraGeneration,
      bootGeneration
    };
    setGraphModeNote("Opening the selected neighborhood.");
    return;
  }
  if (intent.mode === "shortcut") {
    startupMode = false;
    requestCamera({ mode: intent.cluster ? "fit_cluster" : "reset_view", cluster: intent.cluster || "", reason: `boot-shortcut-${intent.shortcut || "unknown"}`, bootGeneration });
    return;
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
              || ele.hasClass("preview-node")
              || ele.hasClass("preview-neighbor")
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
    { selector: "node.selected-node", style: { "border-width": 6, "border-color": "#fff6e8", "border-opacity": 1, "shadow-blur": 58, "shadow-opacity": 1, "shadow-color": "#fff0d8", "z-index": 24 } },
    { selector: "node.neighbor-node", style: { "border-width": 4, "border-color": "rgba(244, 239, 232, 0.92)", "border-opacity": 0.94, "shadow-blur": 34, "shadow-opacity": 0.88, "shadow-color": "#f4efe8", "background-opacity": 0.98, "z-index": 18 } },
    { selector: "node.preview-node", style: { "border-width": 5, "border-color": "#72d6d1", "shadow-blur": 52, "shadow-opacity": 1, width: "mapData(johnny_core_score, 1, 10, 28, 74)", height: "mapData(johnny_core_score, 1, 10, 28, 74)" } },
    { selector: "node.preview-neighbor", style: { "border-width": 3, "border-color": "rgba(114,214,209,0.85)", "shadow-opacity": 0.64 } },
    { selector: "node.outlier-preview-node", style: { "border-width": 6, "border-style": "dashed", "border-color": "#d8b15d", "border-opacity": 1, "shadow-blur": 62, "shadow-opacity": 1, "shadow-color": "#d8b15d", "background-opacity": 0.98, width: "mapData(johnny_core_score, 1, 10, 34, 78)", height: "mapData(johnny_core_score, 1, 10, 34, 78)", "z-index": 28 } },
    { selector: "node.outlier-candidate", style: { "border-width": 4, "border-color": "rgba(216, 177, 93, 0.92)", "border-opacity": 0.96, "shadow-blur": 34, "shadow-opacity": 0.96, "shadow-color": "#d8b15d", "background-opacity": 0.96, "z-index": 20 } },
    { selector: "node.loose-match-neighbor", style: { "border-width": 4, "border-color": "rgba(216, 177, 93, 0.92)", "border-opacity": 0.96, "shadow-blur": 34, "shadow-opacity": 0.92, "shadow-color": "#d8b15d", "background-opacity": 0.96, "z-index": 20 } },
    { selector: "edge.selected-link", style: { width: 4, "line-style": "solid", "line-color": "rgba(255, 248, 235, 0.96)", opacity: 0.96, "z-index": 20 } },
    { selector: "edge.neighbor-link", style: { width: 3, "line-style": "solid", "line-color": "rgba(244, 239, 232, 0.82)", opacity: 0.86, "z-index": 18 } },
    { selector: "edge.outlier-preview-edge", style: { "line-style": "dashed", width: 2, "line-color": "rgba(216, 177, 93, 0.58)", opacity: 0.42 } },
    { selector: "edge.preview-outlier", style: { "line-style": "dashed", width: 2, "line-color": "rgba(216, 177, 93, 0.62)", opacity: 0.52, "z-index": 18 } },
    { selector: "edge.loose-match-edge", style: { "line-style": "dashed", width: 2.5, "line-color": "rgba(216, 177, 93, 0.7)", opacity: 0.62, "z-index": 19 } },
    { selector: "node.compare-node", style: { "border-width": 4, "border-color": "#72d6d1", "shadow-blur": 40, "shadow-opacity": 0.9 } },
    { selector: ".top-node", style: { "font-size": 11 } },
    { selector: ".startup-focus", style: { opacity: 1 } },
    { selector: "node.startup-muted", style: { opacity: 0.14, "shadow-opacity": 0.05, "border-opacity": 0.08 } },
    { selector: "edge.startup-muted", style: { opacity: 0.08 } },
    { selector: ".preview-muted", style: { opacity: 0.24 } },
    { selector: ".faded", style: { opacity: 0.18 } },
    { selector: "edge.faded", style: { opacity: 0.08 } },
    { selector: ".highlight", style: { opacity: 1, "z-index": 10 } },
    { selector: ".filtered-out", style: { opacity: 0 } }
  ];
}

function buildGraphCaches() {
  nodeLookup = new Map();
  neighborLookup = new Map();
  cy.nodes().forEach((node) => {
    nodeLookup.set(node.id(), node);
    neighborLookup.set(node.id(), node.closedNeighborhood());
  });
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
  if (selectedNode && !Number(selectedNode.data("is_outlier") || 0) && !selectedNode.scratch("_visibleDesired")) clearSelection();
  if (!selectedNode && !compareTargetNode && startupMode) {
    updateStartupFocus();
  } else {
    refreshLabelSet();
  }
  renderMovieBrowser();
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
  clearTemporaryOutlierPreview();
  selectedOutlierTitleId = null;
  setBrowserMode("mapped");
  startupMode = true;
  initialCameraApplied = false;
  userHasSelectedTitleInCurrentFilter = false;
  userInteractionGeneration += 1;
  cancelPendingCamera("reset-filters");
  compareMode = false;
  if (controls.compareToggle) {
    controls.compareToggle.setAttribute("aria-pressed", "false");
    controls.compareToggle.classList.remove("is-active");
  }
  applyFilters();
  requestCamera({ mode: "reset_view", reason: "reset-filters" });
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
  clearTemporaryOutlierPreview();
  selectedOutlierTitleId = null;
  startupMode = true;
  userHasSelectedTitleInCurrentFilter = false;
  userInteractionGeneration += 1;
  if (controls.compareToggle) {
    controls.compareToggle.setAttribute("aria-pressed", "false");
    controls.compareToggle.classList.remove("is-active");
  }
  clearSelection();
  cancelPendingCamera("reset-view");
  applyFilters();
  requestCamera({ mode: "reset_view", reason: "reset-view" });
}

function runGraphLayout() {
  if (!cy.nodes().filter((node) => node.scratch("_visibleDesired")).length) return;
  if (currentLayout && currentLayout.stop) {
    console.debug("layout stop requested", { timestamp: cameraTimestamp(), reason: "restart", layoutRunGeneration });
    currentLayout.stop();
  }
  layoutRunGeneration += 1;
  const thisLayoutGeneration = layoutRunGeneration;
  graphReady = false;
  layoutReady = false;
  cameraBusy = false;
  console.debug("layout start", {
    timestamp: cameraTimestamp(),
    generation: thisLayoutGeneration,
    visibleNodeCount,
    selectedTitleId,
    browserMode,
    isFullMapMode
  });
  console.time("layout");
  const layout = cy.layout({
    name: "cose",
    animate: false,
    fit: false,
    randomize: false,
    refresh: visibleNodeCount > 900 ? 48 : 24,
    nodeRepulsion: visibleNodeCount > 900 ? 5400 : 6200,
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
    numIter: visibleNodeCount > 900 ? 650 : 1800,
    initialTemp: 110,
    coolingFactor: 0.94,
    minTemp: 1,
    componentSpacing: 38
  });
  currentLayout = layout;
  cy.one("layoutready", () => {
    console.debug("layout ready", {
      timestamp: cameraTimestamp(),
      generation: thisLayoutGeneration,
      visibleNodeCount,
      selectedTitleId
    });
  });
  cy.one("layoutstop", () => {
    console.timeEnd("layout");
    if (thisLayoutGeneration !== layoutRunGeneration) {
      console.debug("layout stop skipped", {
        timestamp: cameraTimestamp(),
        generation: thisLayoutGeneration,
        currentGeneration: layoutRunGeneration,
        reason: "stale-layout"
      });
      return;
    }
    currentLayout = null;
    layoutReady = true;
    console.debug("layout stop", {
      timestamp: cameraTimestamp(),
      generation: thisLayoutGeneration,
      selectedTitleId,
      selectedOutlierTitleId,
      userHasSelectedTitleInCurrentFilter,
      userInteractionGeneration,
      visibleNodeCount
    });
    if (!selectedNode && startupMode && !userHasSelectedTitleInCurrentFilter) {
      updateStartupFocus();
      setGraphModeNote("Start with one neighborhood. Pick a starting point, or click a title to pull its nearby taste cluster into focus.");
    }
    flushCameraRequestAfterLayout(thisLayoutGeneration);
    if (pendingFocusRequest && !pendingCameraAction) {
      console.debug("layout stop handing off to pending focus", {
        timestamp: cameraTimestamp(),
        generation: thisLayoutGeneration,
        pendingFocusRequest,
        bootIntent: graphBootIntent
      });
      markGraphReady();
    }
  });
  layout.run();
}

function markGraphReady() {
  graphReady = true;
  hideGraphLoading("graph-ready");
  flushPendingFocus();
}

function requestCamera(action) {
  const actionWithMeta = {
    priority: CAMERA_PRIORITIES[action?.mode] || 0,
    graphGeneration,
    filterGeneration,
    cameraGeneration,
    selectionGeneration,
    userCameraGeneration,
    userInteractionGeneration,
    ...action,
  };
  logCameraEvent("REQUEST", {
    mode: actionWithMeta?.mode,
    titleId: actionWithMeta?.titleId,
    cluster: actionWithMeta?.cluster,
    reason: actionWithMeta?.reason,
    priority: actionWithMeta?.priority,
    generation: actionWithMeta?.cameraGeneration,
    userCameraGeneration: actionWithMeta?.userCameraGeneration,
    currentGeneration: cameraGeneration,
    filterGeneration: actionWithMeta?.filterGeneration,
    currentFilterGeneration: filterGeneration,
    selectedTitleId,
    selectedOutlierTitleId,
    userHasSelectedTitleInCurrentFilter,
    graphReady,
    layoutReady,
    visibleNodeCount
  });
  if (actionWithMeta.mode === "selected_title_focus") {
    if (pendingCameraAction && pendingCameraAction.priority < actionWithMeta.priority) {
      logCameraEvent("CANCEL", {
        canceledMode: pendingCameraAction.mode,
        canceledReason: pendingCameraAction.reason,
        nextMode: actionWithMeta.mode,
        nextReason: actionWithMeta.reason,
      });
    }
    pendingCameraAction = null;
  } else if (
    pendingCameraAction &&
    pendingCameraAction.priority > actionWithMeta.priority &&
    pendingCameraAction.filterGeneration === actionWithMeta.filterGeneration
  ) {
    logCameraEvent("SKIP", {
      mode: actionWithMeta.mode,
      reason: actionWithMeta.reason,
      priority: actionWithMeta.priority,
      currentPriority: pendingCameraAction.priority,
      selectedTitleId,
      userHasSelectedTitleInCurrentFilter,
    });
    return;
  }
  pendingCameraAction = actionWithMeta;
  flushCameraRequestAfterLayout();
}

function flushCameraRequestAfterLayout(layoutGeneration = layoutRunGeneration) {
  if (!pendingCameraAction || !graphDataLoaded || !layoutReady || cameraBusy) return;
  window.clearTimeout(cameraFlushTimer);
  const flushInteractionGeneration = userInteractionGeneration;
  const delay = pendingCameraAction?.mode === "selected_title_focus" || pendingCameraAction?.mode === "outlier_preview" ? 0 : 180;
  cameraFlushTimer = window.setTimeout(() => {
    if (!pendingCameraAction || !graphDataLoaded || !layoutReady || cameraBusy) return;
    if (layoutGeneration !== layoutRunGeneration) {
      logCameraEvent("SKIP", {
        mode: pendingCameraAction.mode,
        reason: pendingCameraAction.reason,
        skippedReason: "stale-layout-generation",
        layoutGeneration,
        currentLayoutGeneration: layoutRunGeneration,
      });
      return;
    }
    const action = pendingCameraAction;
    if (flushInteractionGeneration !== userInteractionGeneration || action.userInteractionGeneration !== userInteractionGeneration) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
        skippedReason: "stale-user-interaction-generation",
        generation: action.userInteractionGeneration,
        currentUserInteractionGeneration: userInteractionGeneration,
        userCameraGeneration: action.userCameraGeneration,
        currentUserCameraGeneration: userCameraGeneration,
      });
      pendingCameraAction = null;
      return;
    }
    if (action.mode === "selected_title_focus" && action.userCameraGeneration !== userCameraGeneration) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
        skippedReason: "stale-user-camera-generation",
        userCameraGeneration: action.userCameraGeneration,
        currentUserCameraGeneration: userCameraGeneration,
      });
      pendingCameraAction = null;
      return;
    }
    if (action.cameraGeneration !== cameraGeneration) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
      priority: action.priority,
      generation: action.cameraGeneration,
      userCameraGeneration: action.userCameraGeneration,
      currentGeneration: cameraGeneration,
      skippedReason: "stale-camera-generation"
      });
      pendingCameraAction = null;
      return;
    }
    if (action.graphGeneration !== graphGeneration || action.filterGeneration !== filterGeneration) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
      priority: action.priority,
      generation: action.cameraGeneration,
      userCameraGeneration: action.userCameraGeneration,
      currentGeneration: cameraGeneration,
        filterGeneration: action.filterGeneration,
        currentFilterGeneration: filterGeneration,
        skippedReason: "stale-generation",
      });
      pendingCameraAction = null;
      return;
    }
    if (userHasSelectedTitleInCurrentFilter && (action.mode === "initial_cluster_fit" || action.mode === "fit_cluster")) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
      priority: action.priority,
      userCameraGeneration: action.userCameraGeneration,
      selectedTitleId,
      userHasSelectedTitleInCurrentFilter,
        skippedReason: "user-selection-wins",
      });
      pendingCameraAction = null;
      return;
    }
    if (action.mode !== "selected_title_focus" && selectedTitleId && selectedNode && selectedNode.visible()) {
      logCameraEvent("SKIP", {
        mode: action.mode,
        reason: action.reason,
        priority: action.priority,
        titleId: action.titleId,
        skippedReason: "selection-already-active"
      });
      pendingCameraAction = null;
      return;
    }
    pendingCameraAction = null;
    applyGraphCamera(action);
    if (!initialCameraApplied && (action.mode === "initial_cluster_fit" || action.mode === "reset_view")) {
      initialCameraApplied = true;
      initialCenterDone = true;
    }
  }, 180);
}

function applyGraphCamera(action) {
  if (!cy || !action) return;
  console.time(`camera:${action.mode}`);
  cameraBusy = true;
  cameraFocusElements = null;
  const beforeZoom = cy.zoom();
  const container = cy.container();
  const rect = container?.getBoundingClientRect();
  const width = rect?.width || container?.clientWidth || cy.width();
  const height = rect?.height || container?.clientHeight || cy.height();
  const finish = () => {
    cameraBusy = false;
    console.timeEnd(`camera:${action.mode}`);
    logCameraEvent("APPLIED", {
      mode: action.mode,
      reason: action.reason,
      titleId: action.titleId,
      cluster: action.cluster,
      priority: action.priority,
      generation: action.cameraGeneration,
      userCameraGeneration: action.userCameraGeneration,
      userInteractionGeneration: action.userInteractionGeneration,
      selectedTitleId,
      userHasSelectedTitle: userHasSelectedTitleInCurrentFilter,
      graphReady,
      layoutReady,
      visibleNodeCount,
      width,
      height,
      zoomBefore: beforeZoom,
      zoomAfter: cy.zoom()
    });
    markGraphReady();
  };
  logCameraEvent("APPLY", {
    mode: action.mode,
    titleId: action.titleId,
    cluster: action.cluster,
    reason: action.reason,
    priority: action.priority,
    generation: action.cameraGeneration,
    userCameraGeneration: action.userCameraGeneration,
    currentGeneration: cameraGeneration,
    actionFilterGeneration: action.filterGeneration,
    currentFilterGeneration: filterGeneration,
    selectedTitleId,
    userHasSelectedTitleInCurrentFilter,
    graphReady,
    layoutReady,
    visibleNodeCount,
    width,
    height,
    isFullMapMode,
    zoomBefore: beforeZoom,
    selectedOutlierTitleId
  });
  if (action.mode === "full_map") {
    fitFullMapElements(true);
    window.setTimeout(() => finish({ path: "full-map" }), 520);
    return;
  }
  if (action.mode === "outlier_preview") {
    const node = cy.getElementById(String(action.titleId || ""));
    const candidateNodes = cy.nodes(".loose-match-neighbor, .outlier-candidate").filter((candidate) => candidate.visible());
    const looseEdges = cy.edges(".loose-match-edge, .preview-outlier").filter((edge) => edge.visible());
    const focusElements = node.union(candidateNodes).union(looseEdges);
    if (!node || !node.length || !focusElements.length) {
      logCameraEvent("SKIP", { mode: action.mode, reason: "outlier-preview-missing", titleId: action.titleId });
      finish();
      return;
    }
    cameraFocusElements = focusElements;
    focusElementsOnAnchor(focusElements, node, { padding: 96, minZoom: 0.98, maxZoom: 1.16, animate: true, label: "outlier-preview" });
    window.setTimeout(() => finish({ path: "outlier-preview" }), 480);
    return;
  }
  if (action.mode === "selected_title_focus") {
    const node = cy.getElementById(String(action.titleId || ""));
    if (!node || !node.length || !node.visible()) {
      logCameraEvent("SKIP", { mode: action.mode, reason: "node-missing-or-hidden", titleId: action.titleId });
      finish();
      return;
    }
    const position = node.position();
    const neighborhood = node.closedNeighborhood().filter((ele) => ele.visible());
    const bbox = neighborhood.renderedBoundingBox({ includeLabels: false });
    cameraFocusElements = neighborhood;
    console.debug("camera target node", {
      titleId: action.titleId,
      x: position?.x,
      y: position?.y,
      rectWidth: width,
      rectHeight: height,
      bbox,
      appliedPadding: 88,
      path: isFullMapMode ? "full-map-selected-focus" : "normal-selected-focus",
      isFullMapMode,
    });
    if (currentLayout && currentLayout.stop) {
      console.debug("layout stop requested", {
        reason: "selected-focus",
        titleId: action.titleId,
        generation: layoutRunGeneration
      });
      currentLayout.stop();
      currentLayout = null;
    }
    if (isFullMapMode) {
      focusNeighborhood(node, { smooth: true, minZoom: 1.08, maxZoom: 1.18 });
    } else {
      focusElementsOnAnchor(neighborhood.union(node), node, {
        padding: 96,
        minZoom: 1.02,
        maxZoom: 1.18,
        animate: true,
        label: "selected-title-focus"
      });
    }
    window.setTimeout(() => finish({ path: isFullMapMode ? "full-map-selected-focus" : "normal-selected-focus" }), 480);
    return;
  }
  if (action.mode === "fit_cluster" || action.mode === "initial_cluster_fit") {
    const cluster = action.cluster || controls.cluster.value;
    const nodes = cy.nodes().filter((node) => node.visible() && node.data("cluster") === cluster);
    const edges = cy.edges().filter((edge) => edge.visible() && nodes.contains(edge.source()) && nodes.contains(edge.target()));
    if (nodes.length) {
      cameraFocusElements = nodes.union(edges);
      fitElements(nodes.union(edges), 88, 0.78, 0.98, true, null, false);
      window.setTimeout(() => finish({ path: "cluster-fit" }), 440);
      return;
    }
    logCameraEvent("SKIP", { mode: action.mode, reason: "cluster-empty", cluster });
    finish();
    return;
  }
  cameraFocusElements = null;
  fitMainCluster(true);
  window.setTimeout(() => finish({ path: "reset-view" }), 440);
}

function flushPendingFocus() {
  if (!pendingFocusRequest) return;
  const pending = pendingFocusRequest;
  pendingFocusRequest = null;
  console.debug("flushPendingFocus", {
    timestamp: cameraTimestamp(),
    pending,
    currentBootGeneration: bootGeneration,
    currentUserCameraGeneration: userCameraGeneration,
    layoutReady,
    graphReady
  });
  if (pending.userCameraGeneration !== userCameraGeneration) {
    console.debug("pending focus skipped", {
      titleId: pending.titleId,
      reason: "stale-user-camera-generation",
      pendingUserCameraGeneration: pending.userCameraGeneration,
      userCameraGeneration
    });
    return;
  }
  if (pending.bootGeneration && pending.bootGeneration !== bootGeneration) {
    console.debug("pending focus skipped", {
      titleId: pending.titleId,
      reason: "stale-boot-generation",
      pendingBootGeneration: pending.bootGeneration,
      bootGeneration
    });
    return;
  }
  focusTitleOnGraph(pending.titleId, {
    forceSelect: true,
    deferred: true,
    smooth: true,
    reason: pending.reason || "pending-focus",
    queuedUserCameraGeneration: pending.userCameraGeneration
  });
}

function fitVisibleGraph(maxZoomCap = 0.88) {
  const visible = cy.elements().filter((ele) => ele.visible() && !ele.hasClass("filtered-out"));
  if (!visible.length) return;
  fitElements(visible, 58, 0.66, maxZoomCap, true, null, false);
}

function fullMapElements() {
  const nodes = cy.nodes().filter((node) => node.visible() && isMeaningfulFullMapNode(node));
  const edges = cy.edges().filter((edge) => edge.visible() && nodes.contains(edge.source()) && nodes.contains(edge.target()));
  return nodes.union(edges);
}

function fitMainCluster(useAnimation = false) {
  if (selectedNode && selectedNode.visible()) {
    const neighborhood = selectedNode.closedNeighborhood().filter((ele) => ele.visible());
    fitElements(neighborhood, 88, 0.86, 1.04, useAnimation, null, false);
    return;
  }
  const core = mainStrongComponent();
  if (core && core.length) {
    const neighborhood = core.union(core.connectedEdges().filter((edge) => edge.data("edge_type") !== "soft" && edge.visible()));
    fitElements(neighborhood, 76, 0.7, 0.9, useAnimation, null, false);
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
  fitElements(focusNodes.union(focusEdges), 92, 0.68, 0.84, true, null, false);
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

function keepGraphInView(targetElements = null) {
  if (panGuard || !cy || cameraBusy) return;
  const visibleNodes = targetElements
    ? targetElements.nodes ? targetElements.nodes().filter((node) => node.visible()) : targetElements.filter((node) => node.visible())
    : (cameraFocusElements ? cameraFocusElements.nodes().filter((node) => node.visible()) : cy.nodes().filter((node) => node.visible()));
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
  selectedTitleId = null;
  selectedOutlierTitleId = null;
  userHasSelectedTitleInCurrentFilter = false;
  compareTargetNode = null;
  expandedNeighborhood = false;
  clearTemporaryOutlierPreview();
  clearTemporaryPreview();
  cy.elements().removeClass("highlight faded selected-node compare-node");
  if (startupMode) {
    updateStartupFocus();
  } else {
    cy.elements().removeClass("startup-focus startup-muted");
    refreshLabelSet();
  }
  renderMovieBrowser();
  loadSuggestedAsks(null, "clear-selection");
  setBrowserHelperMessage();
  document.querySelector("#selectedAskActions").hidden = true;
  if (controls.focusSelected) controls.focusSelected.disabled = true;
  if (controls.focusSelectedOverlay) controls.focusSelectedOverlay.disabled = true;
  setGraphModeNote(compareMode ? "Compare mode is on. Choose a title, then click another one to compare neighborhoods." : "Browse the map, then click a title to pull its neighborhood into focus.");
  document.querySelector("#details").innerHTML = `
    <p class="eyebrow">Taste context</p>
    <h2>Explore the map</h2>
    <p>Click a title for its neighborhood, taste signals, and nearby connections. Ask from here without leaving the map.</p>
    <div class="panel-hint">
      <strong>Default view</strong>
      <span>The graph only renders mapped enriched titles so the neighborhoods stay readable and responsive.</span>
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
    requestCamera({ mode: "selected_title_focus", titleId: selectedNode.id(), reason: "compare-mode" });
    setGraphModeNote(`Comparing ${selectedNode.data("title")} with ${node.data("title")}.`);
    return;
  }
  focusTitleOnGraph(node.id(), { forceSelect: true, smooth: true, reason: "graph-tap" });
}

function showDetails(node, options = {}) {
  const keepExpanded = options.keepExpanded || false;
  startupMode = false;
  controls.entryPanel.hidden = true;
  selectedNode = node;
  selectedTitleId = node.id();
  selectedOutlierTitleId = null;
  compareTargetNode = null;
  expandedNeighborhood = keepExpanded ? expandedNeighborhood : false;
  clearTemporaryOutlierPreview();
  applySelectionHighlight();
  const data = node.data();
  const connectedEdges = node.connectedEdges()
    .filter((edge) => edge.visible())
    .sort((a, b) => Number(b.data("confidence") || 0) - Number(a.data("confidence") || 0));
  const strongMatches = renderConnectionList(node, connectedEdges.filter((edge) => edge.data("edge_type") !== "soft"));
  const softMatches = renderConnectionList(node, connectedEdges.filter((edge) => edge.data("edge_type") === "soft"));
  document.querySelector("#details").innerHTML = `
    <p class="eyebrow">${data.cluster}</p>
    <h2>${data.title} ${data.year ? `<span>${data.year}</span>` : ""}</h2>
    <p><strong>${data.source}</strong> · ${data.enrichment_status === "enriched" ? "Enriched" : "Pending enrichment"}</p>
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
  syncBrowserSelection(node.id(), { smooth: true });
  loadSuggestedAsks(data.id, "selection");
  renderSelectedAskActions(data.title);
  setBrowserHelperMessage();
  if (controls.focusSelected) controls.focusSelected.disabled = false;
  if (controls.focusSelectedOverlay) controls.focusSelectedOverlay.disabled = false;
  setGraphModeNote(`Focused on ${data.title}. Explore its neighborhood or ask from this context.`);
}

function renderMovieBrowser() {
  console.time("render movie browser");
  if (isFullMapMode) {
    controls.browserList.innerHTML = "<p class=\"muted-text\">Full map mode is focused on the graph. Exit full map to browse titles.</p>";
    browserRowLookup = new Map();
    console.timeEnd("render movie browser");
    return;
  }
  const query = controls.browserSearch.value.trim().toLowerCase();
  const mappedNodes = getMappedBrowserNodes();
  const recentNodes = applyBrowserQuickFilter(mappedNodes, "recent");
  const johnnyNodes = applyBrowserQuickFilter(mappedNodes, "johnny");
  const weirdNodes = applyBrowserQuickFilter(mappedNodes, "weird");
  const emotionNodes = applyBrowserQuickFilter(mappedNodes, "emotion");
  const browserSets = {
    mapped: mappedNodes,
    recent: recentNodes,
    johnny: johnnyNodes,
    weird: weirdNodes,
    emotion: emotionNodes,
  };
  const nodes = browserSets[browserMode] || mappedNodes;
  controls.allMappedTab.textContent = `All Mapped (${mappedNodes.length})`;
  controls.recentTab.textContent = `Recently Added (${recentNodes.length})`;
  controls.johnnyTab.textContent = `High Johnny-core (${johnnyNodes.length})`;
  controls.weirdTab.textContent = `High Weirdness (${weirdNodes.length})`;
  controls.emotionTab.textContent = `High Emotional Weight (${emotionNodes.length})`;
  const filtered = nodes.filter((node) => {
    const data = node.data();
    return !query || `${data.title} ${data.year || ""} ${data.cluster || ""} ${(data.tags || []).join(" ")} ${data.source || ""}`.toLowerCase().includes(query);
  });
  controls.browserList.innerHTML = filtered.map((node) => {
    const data = node.data();
    const selected = selectedTitleId && selectedTitleId === node.id();
    return `
      <button type="button" class="movie-browser-item ${selected ? "selected" : ""}" data-node-id="${node.id()}" data-title-id="${node.id()}">
        <span>
          <strong>${data.title}</strong>
          <small>${data.year || "n/a"} · ${data.cluster || "No cluster"}</small>
        </span>
        ${scoreCircles(data)}
      </button>
    `;
  }).join("") || `<p class="muted-text">No mapped titles match this view.</p>`;
  browserRowLookup = new Map();
  controls.browserList.querySelectorAll("[data-node-id]").forEach((button) => {
    browserRowLookup.set(String(button.dataset.titleId || button.dataset.nodeId), button);
    button.addEventListener("mouseenter", () => {
      hoverPreviewLocked = true;
      scheduleListPreview(button.dataset.titleId || button.dataset.nodeId, 45);
    });
    button.addEventListener("mouseleave", () => {
      hoverPreviewLocked = false;
      cancelListPreview();
      clearTemporaryPreview();
    });
    button.addEventListener("focus", () => {
      hoverPreviewLocked = true;
      scheduleListPreview(button.dataset.titleId || button.dataset.nodeId, 25);
    });
    button.addEventListener("blur", () => {
      hoverPreviewLocked = false;
      cancelListPreview();
      clearTemporaryPreview();
    });
    button.addEventListener("click", () => {
      focusTitleOnGraph(button.dataset.titleId || button.dataset.nodeId, { forceSelect: true, smooth: true, reason: "browser-click" });
    });
  });
  syncBrowserRowState();
  console.timeEnd("render movie browser");
}

function getMappedBrowserNodes() {
  return cy.nodes()
    .filter((node) => {
      const enriched = node.data("enrichment_status") === "enriched";
      const isOutlier = Boolean(Number(node.data("is_outlier") || 0));
      return enriched && !isOutlier && node.scratch("_visibleDesired");
    })
    .sort((a, b) => a.data("title").localeCompare(b.data("title")));
}

function applyBrowserQuickFilter(nodes, mode = "mapped") {
  if (mode === "recent") {
    return [...nodes].sort((a, b) => String(parseRecencyValue(b)).localeCompare(String(parseRecencyValue(a)))).slice(0, 120);
  }
  if (mode === "johnny") {
    return [...nodes]
      .filter((node) => Number(node.data("johnny_core_score") || 0) >= 7)
      .sort((a, b) => comparePreferredNodes(a, b, ["johnny_core_score", "edge_count", "weirdness_score", "emotional_weight_score"]));
  }
  if (mode === "weird") {
    return [...nodes]
      .filter((node) => Number(node.data("weirdness_score") || 0) >= 7)
      .sort((a, b) => comparePreferredNodes(a, b, ["weirdness_score", "edge_count", "johnny_core_score", "updated_at"]));
  }
  if (mode === "emotion") {
    return [...nodes]
      .filter((node) => Number(node.data("emotional_weight_score") || 0) >= 7)
      .sort((a, b) => comparePreferredNodes(a, b, ["emotional_weight_score", "edge_count", "johnny_core_score", "updated_at"]));
  }
  return [...nodes];
}

function handleBrowserScroll() {
  if (hoverPreviewLocked || isFullMapMode) return;
  window.clearTimeout(browserScrollTimer);
  browserScrollTimer = window.setTimeout(() => {
    previewCenteredBrowserItem();
  }, 140);
}

function previewCenteredBrowserItem() {
  const buttons = [...controls.browserList.querySelectorAll("[data-node-id]")];
  if (!buttons.length) return;
  const containerBox = controls.browserList.getBoundingClientRect();
  const containerCenter = containerBox.top + containerBox.height / 2;
  let bestButton = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const button of buttons) {
    const box = button.getBoundingClientRect();
    if (box.bottom < containerBox.top || box.top > containerBox.bottom) continue;
    const center = box.top + box.height / 2;
    const distance = Math.abs(center - containerCenter);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestButton = button;
    }
  }
  if (bestButton) scheduleListPreview(bestButton.dataset.titleId || bestButton.dataset.nodeId, 35);
}

function cancelListPreview() {
  window.clearTimeout(listPreviewTimer);
}

function scheduleListPreview(nodeId, delay = 40) {
  cancelListPreview();
  listPreviewTimer = window.setTimeout(() => {
    previewNodeFromList(nodeId);
  }, delay);
}

function previewNodeFromList(nodeId) {
  if (!cy || isFullMapMode) return;
  const normalizedId = String(nodeId);
  const node = nodeLookup.get(normalizedId);
  console.debug("previewNodeFromList", { titleId: normalizedId, found: Boolean(node && node.length) });
  if (!node || !node.length || !node.visible()) {
    clearTemporaryPreview();
    return;
  }
  applyTemporaryPreview(node);
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
    <button type="button" data-intent="similar" data-query="What is similar to ${title}?" data-question="What is similar to ${title}?">Similar to this</button>
    <button type="button" data-intent="weirder" data-query="Give me weirder picks like ${title}." data-question="Give me weirder picks like ${title}.">Weirder picks</button>
    <button type="button" data-intent="heavier" data-query="Give me emotionally heavier picks like ${title}." data-question="Give me emotionally heavier picks like ${title}.">Emotionally heavier</button>
    <button type="button" data-intent="why_connects" data-query="Why does ${title} connect to these?" data-question="Why does ${title} connect to these?">Why it connects</button>
  `;
  panel.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => askTasteGraph(button.dataset.question, { intent: button.dataset.intent || "" }));
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
    loadSuggestedAsks(selectedTitleId ? Number(selectedTitleId) : null, "new-prompts");
  });
  controls.resetAsk.addEventListener("click", resetAskPanel);
  loadSuggestedAsks(null, "init");
  syncAskResetState();
}

async function loadSuggestedAsks(selectedTitleId = null, reason = "selection") {
  const container = document.querySelector("#askPrompts");
  const requestId = ++suggestedAskRequestId;
  console.debug("fetchSuggestedAsks", { reason, selectedTitleId, requestId });
  container.innerHTML = "<span class=\"muted-text\">Loading suggestions...</span>";
  const params = selectedTitleId ? `?selected_title_id=${encodeURIComponent(selectedTitleId)}` : "";
  const data = await fetch(`/api/suggested-asks${params}`).then((response) => response.json());
  if (requestId !== suggestedAskRequestId) {
    console.debug("fetchSuggestedAsks ignored", { reason, selectedTitleId, requestId });
    return;
  }
  console.debug("fetchSuggestedAsks applied", { reason, selectedTitleId, requestId });
  container.innerHTML = "";
  (data.suggestions || []).forEach((suggestion) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = suggestion.label;
    button.dataset.question = suggestion.question;
    button.dataset.query = suggestion.question;
    button.dataset.intent = suggestion.intent || "";
    button.dataset.selectedTitleId = selectedTitleId ? String(selectedTitleId) : "";
    button.dataset.titleName = selectedNode && selectedTitleId && String(selectedNode.id()) === String(selectedTitleId)
      ? (selectedNode.data("title") || "")
      : "";
    button.addEventListener("click", () => askTasteGraph(button.dataset.question, {
      intent: button.dataset.intent || "",
      selectedTitleId: button.dataset.selectedTitleId || null
    }));
    container.append(button);
  });
}

async function askTasteGraph(question, options = {}) {
  if (!question) return;
  const explainWithAi = Boolean(options.explainWithAi);
  const intent = options.intent || "";
  const explicitSelectedTitleId = options.selectedTitleId || null;
  const input = document.querySelector("#graphAskQuestion");
  const answer = document.querySelector("#graphAskAnswer");
  const button = controls.askButton;
  const status = controls.askStatus;
  const requestId = ++askRequestId;
  askExplainRequestId += 1;
  const selectedTitleForAsk = explicitSelectedTitleId != null
    ? Number(explicitSelectedTitleId)
    : (selectedNode ? Number(selectedNode.id()) : null);
  console.debug("ask submit", { requestId, selected_title_id: selectedTitleForAsk, intent, query: question, explainWithAi });
  answer.hidden = false;
  input.value = question;
  syncAskResetState(true);
  button.disabled = true;
  button.classList.add("is-loading");
  button.textContent = "Thinking...";
  status.hidden = false;
  status.textContent = explainWithAi ? "Asking AI for a richer explanation..." : "Thinking through graph neighbors...";
  if (answer.hidden || !answer.innerHTML.trim()) {
    answer.innerHTML = explainWithAi ? "<p>Asking AI for a richer explanation...</p>" : "<p>Thinking through graph neighbors...</p>";
  }
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        explain_with_ai: explainWithAi,
        selected_title_id: selectedTitleForAsk,
        intent
      })
    });
    const data = await response.json();
    if (requestId !== askRequestId) {
      console.debug("ask response ignored", { requestId, selected_title_id: selectedTitleForAsk, intent, query: question });
      return;
    }
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "Couldn’t load recommendations");
    }
    console.debug("ask response applied", {
      requestId,
      selected_title_id: selectedTitleForAsk,
      intent: data.intent || intent,
      query: question,
      cached: Boolean(data.cached)
    });
    answer.innerHTML = renderAskAnswer(data);
    activeAskState = {
      question,
      intent: data.intent || intent,
      selectedTitleId: selectedTitleForAsk,
      data
    };
    answer.querySelectorAll("[data-open-node]").forEach((buttonEl) => {
      buttonEl.addEventListener("click", () => focusTitleOnGraph(buttonEl.dataset.openNode, { forceSelect: true, smooth: true, reason: "ask-result" }));
    });
    const explainButton = answer.querySelector("[data-explain-ai]");
    if (explainButton) {
      explainButton.addEventListener("click", () => askExplainWithAi(activeAskState));
    }
  } catch (error) {
    if (requestId === askRequestId) {
      console.debug("ask response failed", { requestId, selected_title_id: selectedTitleForAsk, intent, query: question, error: String(error) });
      showAskInlineError("Couldn’t load recommendations. The previous answer is still visible.");
    }
  } finally {
    if (requestId === askRequestId) {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.textContent = "Ask";
      status.hidden = true;
      syncAskResetState();
    }
  }
}

async function askExplainWithAi(state) {
  if (!state) return;
  const slot = document.querySelector("#aiExplanationContainer");
  if (!slot) return;
  const requestId = ++askExplainRequestId;
  slot.hidden = false;
  slot.innerHTML = "<div class=\"ask-ai-panel loading\"><h4>AI explanation</h4><p>AI explanation loading...</p></div>";
  console.debug("ask explain started", {
    requestId,
    selected_title_id: state.selectedTitleId,
    intent: state.intent,
    query: state.question
  });
  try {
    const response = await fetch("/api/ask/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: state.question,
        selected_title_id: state.selectedTitleId,
        intent: state.intent
      })
    });
    const data = await response.json();
    if (requestId !== askExplainRequestId) {
      console.debug("ask explain ignored", { requestId });
      return;
    }
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "AI explanation unavailable right now.");
    }
    console.debug("ask explain applied", {
      requestId,
      selected_title_id: state.selectedTitleId,
      intent: state.intent
    });
    slot.innerHTML = `
      <div class="ask-ai-panel">
        <h4>${data.title || "AI explanation"}</h4>
        <p>${data.explanation || "AI explanation unavailable right now."}</p>
      </div>
    `;
  } catch (error) {
    if (requestId !== askExplainRequestId) return;
    console.debug("ask explain failed", {
      requestId,
      selected_title_id: state.selectedTitleId,
      intent: state.intent,
      error: String(error),
      previousLocalResultPreserved: true
    });
    slot.innerHTML = "<div class=\"ask-ai-panel error\"><h4>AI explanation</h4><p>AI explanation unavailable right now.</p></div>";
  }
}

function showAskInlineError(message) {
  const answer = document.querySelector("#graphAskAnswer");
  const existing = answer.querySelector(".ask-inline-error");
  if (existing) existing.remove();
  const error = document.createElement("div");
  error.className = "ask-inline-error";
  error.innerHTML = `<p>${message}</p>`;
  answer.prepend(error);
}

function renderAskAnswer(data) {
  const intent = data.intent || "closest";
  const emptyReasons = data.bucket_empty_reasons || {};
  const warnings = data.warnings || [];
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
            <p>${item.year ? `${item.year} · ` : ""}${item.cluster || "No cluster"}${item.edge_type === "bridge" ? " · bridge connection" : item.edge_type === "soft" ? " · looser match" : ""}</p>
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
  const groups = {
    best_matches: {
      key: "best_matches",
      title: "Best matches",
      items: data.best_matches || data.nearby_titles,
      empty: "No strong recommendation set yet."
    },
    weirdest_matches: {
      key: "weirdest_matches",
      title: "Weirder picks",
      items: data.weirdest_matches,
      empty: emptyReasons.weirdest_matches || "No weirder nearby picks yet."
    },
    emotionally_heavier_matches: {
      key: "emotionally_heavier_matches",
      title: "Emotionally heavier",
      items: data.emotionally_heavier_matches,
      empty: emptyReasons.emotionally_heavier_matches || "No heavier nearby matches yet."
    },
    safer_easier_watches: {
      key: "safer_easier_watches",
      title: "Safer / easier",
      items: data.safer_easier_watches,
      empty: emptyReasons.safer_easier_watches || "No easier nearby picks yet."
    },
    bridge_titles: {
      key: "bridge_titles",
      title: "Bridge titles",
      items: data.bridge_titles,
      empty: emptyReasons.bridge_titles || "No bridge titles surfaced yet."
    }
  };
  let order = ["best_matches", "weirdest_matches", "emotionally_heavier_matches", "safer_easier_watches", "bridge_titles"];
  if (intent === "weirder") {
    order = ["weirdest_matches", "best_matches", "bridge_titles"];
  } else if (intent === "heavier") {
    order = ["emotionally_heavier_matches", "best_matches", "bridge_titles"];
  } else if (intent === "safer") {
    order = ["safer_easier_watches", "best_matches", "bridge_titles"];
  } else if (intent === "why_connects") {
    order = ["best_matches", "bridge_titles"];
  } else if (intent === "similar") {
    order = ["best_matches", "bridge_titles", "weirdest_matches"];
  }
  const sections = order.map((key) => {
    const group = groups[key];
    return `
      <section class="ask-result-group ask-result-group-${group.key}">
        <h4>${group.title}</h4>
        <div class="ask-result-list">${list(group.items, group.empty)}</div>
      </section>
    `;
  }).join("");
  return `
    <div class="ask-answer-summary">
      <h3>${data.recommendation || "Taste Graph answer"}</h3>
      <p>${data.why_these_fit || data.why_it_fits || ""}</p>
    </div>
    ${warnings.length ? `<div class="ask-answer-warnings">${warnings.map((warning) => `<p>${warning}</p>`).join("")}</div>` : ""}
    ${data.answer_source === "local_graph" && data.can_explain_with_ai ? `<button class="ai-explain-button" type="button" data-explain-ai>Explain with AI</button>` : ""}
    <div id="aiExplanationContainer" class="ai-explanation-slot" hidden></div>
    <div id="localGraphResultsContainer" class="ask-answer-grid">
      ${sections}
    </div>
    <div class="tag-row">${(data.tags_driving_recommendation || data.tags_that_drove_answer || []).map((tag) => `<em>${tag}</em>`).join("")}</div>
  `;
}

function applySelectionHighlight() {
  clearTemporaryPreview(false);
  applyPersistentHighlightState();
}

function restoreTemporaryOutlierPreviewHighlight() {
  if (!selectedNode || !selectedOutlierTitleId || !Number(selectedNode.data("is_outlier") || 0)) return false;
  const node = temporaryOutlierNodeId ? cy.getElementById(temporaryOutlierNodeId) : selectedNode;
  if (!node || !node.length) return false;
  const tempEdges = cy.edges(".loose-match-edge, .preview-outlier").filter((edge) => edge.visible());
  const candidateNodes = cy.nodes(".loose-match-neighbor, .outlier-candidate").filter((candidate) => candidate.id() !== node.id() && candidate.visible());
  graphRenderMode = "outlier_preview";
  setGraphVisualMode("outlier-preview");
  cy.elements().removeClass("highlight faded selected-node compare-node preview-node preview-neighbor outlier-candidate outlier-preview-node neighbor-node selected-link neighbor-link loose-match-neighbor loose-match-edge");
  cy.elements().addClass("faded");
  const focusSet = node.union(candidateNodes).union(tempEdges);
  focusSet.removeClass("faded").addClass("highlight");
  node.removeClass("faded filtered-out").addClass("outlier-preview-node selected-node highlight");
  candidateNodes.addClass("preview-neighbor outlier-candidate loose-match-neighbor highlight");
  tempEdges.addClass("preview-outlier loose-match-edge");
  scheduleLabelRefresh(20);
  return true;
}

function clearTemporaryOutlierPreview() {
  if (!cy) return;
  if (temporaryOutlierEdgeIds.length) {
    temporaryOutlierEdgeIds.forEach((id) => {
      const edge = cy.getElementById(id);
      if (edge && edge.length) edge.remove();
    });
  }
  temporaryOutlierEdgeIds = [];
  outlierPreviewHiddenNodeIds = [];
  outlierPreviewHiddenEdgeIds = [];
  if (temporaryOutlierNodeId) {
    const node = cy.getElementById(temporaryOutlierNodeId);
    if (node && node.length) {
      node.removeClass("outlier-preview-node outlier-candidate loose-match-neighbor highlight selected-node preview-node preview-neighbor");
      node.removeClass("filtered-out");
      if (Number(node.data("is_outlier") || 0) && !node.scratch("_visibleDesired")) {
        node.style("display", "none");
      }
    }
  }
  temporaryOutlierNodeId = null;
  graphRenderMode = "normal";
  restoreElementDisplayState();
  setGraphVisualMode(selectedNode && !Number(selectedNode.data("is_outlier") || 0) ? "selected" : "default");
}

function normalizedTagSet(data) {
  const values = [];
  const tagLists = [data.tags, data.tone_tags, data.theme_tags, data.style_tags, data.mood_tags, data.recommendation_hooks];
  tagLists.forEach((list) => {
    (list || []).forEach((item) => {
      const normalized = String(item || "").trim().toLowerCase();
      if (normalized) values.push(normalized);
    });
  });
  const context = String(data.closest_viewing_context || "").trim().toLowerCase();
  if (context) {
    context.split(/[;,]/).map((item) => item.trim()).filter(Boolean).forEach((item) => values.push(item));
  }
  return new Set(values);
}

function scoreDistance(left, right) {
  const pairs = [
    ["johnny_core_score", 1.15],
    ["weirdness_score", 1.1],
    ["emotional_weight_score", 1.1],
    ["intensity_score", 0.7],
    ["pacing_score", 0.45]
  ];
  const total = pairs.reduce((sum, [key, weight]) => {
    return sum + Math.abs(Number(left[key] || 0) - Number(right[key] || 0)) * weight;
  }, 0);
  return Math.max(0, 1 - (total / 32));
}

function outlierCandidateScore(outlierNode, mappedNode) {
  const left = outlierNode.data();
  const right = mappedNode.data();
  const leftTags = normalizedTagSet(left);
  const rightTags = normalizedTagSet(right);
  const sharedTags = [...leftTags].filter((tag) => rightTags.has(tag));
  const scoreSimilarity = scoreDistance(left, right);
  const clusterMatch = left.cluster && right.cluster && left.cluster === right.cluster ? 0.22 : 0;
  const sourceBonus = right.source === "plex" ? 0.04 : 0;
  const degreeBonus = Math.min(0.16, mappedNode.connectedEdges().filter((edge) => edge.visible()).length * 0.012);
  const yearGap = Math.abs(Number(left.year || 0) - Number(right.year || 0));
  const yearBonus = Number(left.year || 0) && Number(right.year || 0) ? Math.max(0, 0.08 - Math.min(yearGap, 40) / 650) : 0;
  const sharedTagScore = Math.min(0.48, sharedTags.length * 0.14);
  const total = sharedTagScore + scoreSimilarity * 0.34 + clusterMatch + sourceBonus + degreeBonus + yearBonus;
  return {
    node: mappedNode,
    score: total,
    sharedTags,
    confidence: Math.max(0.28, Math.min(0.78, total)),
    explanation: sharedTags.length
      ? `Loose connection: both touch ${sharedTags.slice(0, 3).join(", ")}, but this title still sits outside the main map.`
      : `Loose connection: similar score profile${clusterMatch ? ` within ${right.cluster}` : ""}, but still a tentative placement.`
  };
}

function computeNearestMappedCandidates(outlierNode, limit = 5) {
  const connected = outlierNode.connectedEdges()
    .map((edge) => {
      const other = edge.source().id() === outlierNode.id() ? edge.target() : edge.source();
      if (!other || !other.length || Number(other.data("is_outlier") || 0) || !other.scratch("_visibleDesired")) return null;
      return {
        node: other,
        score: Number(edge.data("confidence") || edge.data("weight") || 0),
        sharedTags: edge.data("shared_traits") || [],
        confidence: Number(edge.data("confidence") || edge.data("weight") || 0),
        explanation: edge.data("explanation") || "Loose connection preview from the current graph."
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  const used = new Set();
  const candidates = [];
  connected.forEach((candidate) => {
    if (candidates.length >= limit) return;
    if (used.has(candidate.node.id())) return;
    used.add(candidate.node.id());
    candidates.push(candidate);
  });

  if (candidates.length >= limit) return candidates.slice(0, limit);

  cy.nodes()
    .filter((node) => node.visible() && !Number(node.data("is_outlier") || 0) && node.data("enrichment_status") === "enriched")
    .forEach((node) => {
      if (used.has(node.id())) return;
      const scored = outlierCandidateScore(outlierNode, node);
      if (scored.score >= 0.4) {
        candidates.push(scored);
        used.add(node.id());
      }
    });

  return candidates
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(3, limit));
}

function ensureTemporaryOutlierPreview(outlierNode, candidates) {
  clearTemporaryOutlierPreview();
  if (!outlierNode || !outlierNode.length) return cy.collection();
  temporaryOutlierNodeId = outlierNode.id();
  console.debug("temporary outlier preview start", {
    selectedOutlierTitleId,
    selectedTitle: outlierNode.data("title"),
    temporaryOutlierNodeCreated: Boolean(outlierNode && outlierNode.length),
    temporaryOutlierNodeId: outlierNode.id(),
    nearestMappedCandidates: (candidates || []).map((candidate) => ({
      id: candidate.node.id(),
      title: candidate.node.data("title"),
      score: candidate.score,
      confidence: candidate.confidence
    })),
    graphRenderMode,
    currentRenderedNodes: cy.nodes().filter((node) => node.visible()).length,
    currentRenderedEdges: cy.edges().filter((edge) => edge.visible()).length
  });
  outlierNode.style("display", "element");
  outlierNode.scratch("_visibleDesired", true);
  outlierNode.removeClass("filtered-out startup-muted preview-muted faded");
  outlierNode.addClass("outlier-preview-node selected-node highlight");

  const connectedWeakEdges = outlierNode.connectedEdges()
    .filter((edge) => {
      const other = edge.source().id() === outlierNode.id() ? edge.target() : edge.source();
      return other && other.length && other.visible() && !Number(other.data("is_outlier") || 0);
    })
    .sort((a, b) => Number(b.data("confidence") || b.data("weight") || 0) - Number(a.data("confidence") || a.data("weight") || 0))
    .slice(0, 6);
  const connectedCandidateNodes = connectedWeakEdges.connectedNodes().filter((node) => node.id() !== outlierNode.id() && node.visible());
  const candidateNodes = connectedCandidateNodes.length
    ? connectedCandidateNodes
    : cy.collection((candidates || []).map((candidate) => candidate.node));
  if (candidateNodes.length) {
    const positions = candidateNodes.map((node) => node.position());
    const centroid = positions.reduce((acc, pos) => ({ x: acc.x + pos.x, y: acc.y + pos.y }), { x: 0, y: 0 });
    const avgX = centroid.x / positions.length;
    const avgY = centroid.y / positions.length;
    const radius = 110 + positions.length * 8;
    outlierNode.position({ x: avgX + radius * 0.48, y: avgY - radius * 0.22 });
  }

  const candidateEdgeIds = new Set(connectedWeakEdges.map((edge) => edge.id()));
  const newEdges = (candidates || [])
    .filter((candidate) => !candidateEdgeIds.has(`temp-outlier-${outlierNode.id()}-${candidate.node.id()}`))
    .map((candidate) => ({
    group: "edges",
    data: {
      id: `temp-outlier-${outlierNode.id()}-${candidate.node.id()}`,
      source: outlierNode.id(),
      target: candidate.node.id(),
      edge_type: "preview_outlier",
      confidence: candidate.confidence,
      weight: candidate.confidence,
      shared_traits: candidate.sharedTags.slice(0, 4),
      explanation: candidate.explanation
    },
    classes: "outlier-preview-edge"
  }));
  if (newEdges.length) {
    cy.add(newEdges);
  }
  temporaryOutlierEdgeIds = newEdges.map((edge) => edge.data.id);
  const tempEdges = cy.collection(temporaryOutlierEdgeIds.map((id) => cy.getElementById(id)));
  const focusEdges = connectedWeakEdges.union(tempEdges);
  const focusSet = outlierNode.union(candidateNodes).union(focusEdges);
  setGraphVisualMode("outlier-preview");
  graphRenderMode = "outlier_preview";
  cy.elements().removeClass("highlight faded selected-node compare-node preview-node preview-neighbor outlier-candidate outlier-preview-node neighbor-node selected-link neighbor-link loose-match-neighbor loose-match-edge");
  cy.elements().addClass("faded");
  focusSet.removeClass("faded").addClass("highlight");
  outlierNode.addClass("outlier-preview-node selected-node highlight");
  candidateNodes.addClass("preview-neighbor outlier-candidate loose-match-neighbor highlight");
  focusEdges.addClass("preview-outlier loose-match-edge");
  console.debug("temporary outlier preview render", {
    selectedOutlierTitleId,
    selectedOutlierTitle: outlierNode.data("title"),
    isOutlier: Boolean(Number(outlierNode.data("is_outlier") || 0)),
    temporaryNodeObject: {
      id: outlierNode.id(),
      title: outlierNode.data("title"),
      cluster: outlierNode.data("cluster")
    },
    candidateCount: candidateNodes.length,
    candidateTitles: candidateNodes.map((node) => node.data("title")),
    previewEdgeCount: focusEdges.length,
    graphRenderMode,
    selectedOutlierTitleId,
    temporaryOutlierNodeId,
    temporaryOutlierEdges: focusEdges.length,
    candidatesRendered: candidateNodes.length,
    includedInRenderedNodes: Boolean(outlierNode.visible()),
    includedInRenderedLinks: Boolean(focusEdges.length && focusEdges.filter((edge) => edge.visible()).length),
    foregroundDisplayedNodeCount: focusSet.nodes().length,
    foregroundDisplayedLinkCount: focusEdges.length,
    finalDisplayedNodeCount: cy.nodes().filter((node) => node.style("display") !== "none").length,
    finalDisplayedLinkCount: cy.edges().filter((edge) => edge.style("display") !== "none").length,
  });
  scheduleLabelRefresh(20);
  return focusSet;
}

function applyLooseMatchFocus(node) {
  if (!node || !node.length) return cy?.collection() || null;
  const candidates = computeNearestMappedCandidates(node, 6);
  const focusSet = ensureTemporaryOutlierPreview(node, candidates);
  const focusNodeCount = focusSet?.nodes ? focusSet.nodes().length : 0;
  const focusEdgeCount = focusSet?.edges ? focusSet.edges().length : 0;
  console.debug("loose match focus", {
    titleId: node.id(),
    title: node.data("title"),
    candidateCount: candidates.length,
    candidateTitles: candidates.map((candidate) => candidate.node.data("title")),
    foregroundDisplayedNodeCount: focusNodeCount,
    foregroundDisplayedLinkCount: focusEdgeCount,
    graphRenderMode
  });
  return focusSet;
}

function applyPersistentHighlightState() {
  graphRenderMode = selectedNode || compareTargetNode ? "mapped_focus" : "normal";
  setGraphVisualMode((selectedNode || compareTargetNode) ? "selected" : "default");
  cy.elements().removeClass("highlight faded selected-node compare-node startup-focus startup-muted neighbor-node selected-link neighbor-link outlier-candidate outlier-preview-node preview-outlier loose-match-neighbor loose-match-edge");
  if (!selectedNode && !compareTargetNode) return;
  cy.elements().addClass("faded");
  let focusSet = cy.collection();
  if (selectedNode) {
    const primary = expandedNeighborhood ? selectedNode.closedNeighborhood().union(selectedNode.closedNeighborhood().closedNeighborhood()) : selectedNode.closedNeighborhood();
    selectedNode.addClass("selected-node");
    const neighborNodes = selectedNode.neighborhood("node").filter((node) => node.visible());
    const neighborEdges = selectedNode.connectedEdges().filter((edge) => edge.visible());
    neighborNodes.addClass("neighbor-node");
    neighborEdges.addClass("selected-link");
    focusSet = focusSet.union(primary);
  }
  if (compareTargetNode) {
    compareTargetNode.addClass("compare-node");
    const compareNeighborhood = compareTargetNode.closedNeighborhood().filter((ele) => ele.visible());
    compareTargetNode.neighborhood("node").filter((node) => node.visible()).addClass("neighbor-node");
    compareTargetNode.connectedEdges().filter((edge) => edge.visible()).addClass("neighbor-link");
    focusSet = focusSet.union(compareNeighborhood);
  }
  focusSet.removeClass("faded").addClass("highlight");
}

function applyTemporaryPreview(node) {
  if (!node || !node.length || !node.visible()) return;
  if (previewNode && previewNode.id() === node.id()) return;
  clearTemporaryPreview();
  previewNode = node;
  previewTitleId = node.id();
  const neighborhood = (neighborLookup.get(node.id()) || node.closedNeighborhood()).filter((ele) => ele.visible());
  let focus = neighborhood.union(node);
  if (selectedNode && selectedNode.visible()) {
    focus = focus.union(selectedNode.closedNeighborhood().filter((ele) => ele.visible()));
  }
  if (compareTargetNode && compareTargetNode.visible()) {
    focus = focus.union(compareTargetNode.closedNeighborhood().filter((ele) => ele.visible()));
  }
  previewFocusElements = focus;
  previewMutedElements = cy.elements().filter((ele) => ele.visible() && !focus.contains(ele));
  previewMutedElements.addClass("preview-muted");
  previewFocusElements.removeClass("faded").addClass("highlight");
  node.addClass("preview-node");
  neighborhood.nodes().difference(node).addClass("preview-neighbor");
  setPreviewedBrowserItem(node.id());
  scheduleLabelRefresh(20);
}

function clearTemporaryPreview(restoreState = true) {
  cancelListPreview();
  if (previewMutedElements && previewMutedElements.length) {
    previewMutedElements.removeClass("preview-muted");
  }
  if (previewFocusElements && previewFocusElements.length) {
    previewFocusElements.nodes().removeClass("preview-neighbor preview-node");
  }
  previewNode = null;
  previewTitleId = null;
  previewFocusElements = null;
  previewMutedElements = null;
  setPreviewedBrowserItem(null);
  if (restoreState) {
    if (selectedNode || compareTargetNode) {
      applyPersistentHighlightState();
    } else {
      cy?.elements().removeClass("highlight faded");
    }
  }
  scheduleLabelRefresh(20);
}

function setPreviewedBrowserItem(nodeId) {
  browserRowLookup.forEach((button, key) => {
    button.classList.toggle("previewed", Boolean(nodeId && key === String(nodeId)));
  });
}

function syncBrowserRowState() {
  browserRowLookup.forEach((button, key) => {
    button.classList.toggle("selected", Boolean(selectedTitleId && key === String(selectedTitleId)));
    button.classList.toggle("previewed", Boolean(previewTitleId && key === String(previewTitleId)));
  });
}

function syncBrowserSelection(nodeId, options = {}) {
  const row = browserRowLookup.get(String(nodeId));
  if (row) {
    row.scrollIntoView({ block: "center", behavior: options.smooth ? "smooth" : "auto" });
    row.classList.add("selected");
    return true;
  }
  if (controls.browserSearch.value.trim()) {
    setBrowserHelperMessage("Selected title is hidden by the current title search. Clear the search to reveal it in Browse Titles.");
    return false;
  }
  return false;
}

function setBrowserHelperMessage(message = "Hover a title to locate it on the map. Click to focus.") {
  if (!controls.browserHelper) return;
  controls.browserHelper.textContent = message;
}

function focusNode(node, minZoom = 1.08) {
  focusNodeWithRetry(node, minZoom, 0);
}

function focusNodeWithRetry(node, minZoom = 1.08, attempt = 0) {
  if (!node || !node.length) return;
  const position = node.position();
  if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) {
    if (attempt < 6) {
      window.setTimeout(() => focusNodeWithRetry(node, minZoom, attempt + 1), 90);
    }
    return;
  }
  cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), minZoom) }, { duration: 340, easing: "ease-out-cubic" });
}

function focusNeighborhood(node, options = {}) {
  if (!node || !node.length) return;
  const minZoom = options.minZoom ?? 1.08;
  const maxZoom = options.maxZoom ?? 1.18;
  const neighborhood = node.closedNeighborhood().filter((ele) => ele.visible());
  if (neighborhood.length > 1) {
    fitElements(neighborhood, 88, minZoom, maxZoom, options.smooth !== false, neighborhood);
    return;
  }
  focusNode(node, minZoom);
}

function fitElements(elements, padding = 76, minZoom = 0.96, maxZoom = 1.12, animate = true, anchorElements = null, keepInView = false) {
  if (!elements || !elements.length) return;
  const visible = elements.filter((ele) => ele.visible());
  const done = () => {
    if (cy.zoom() < minZoom) cy.zoom(minZoom);
    if (cy.zoom() > maxZoom) cy.zoom(maxZoom);
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

function focusElementsOnAnchor(elements, anchorNode, options = {}) {
  if (!cy || !elements || !elements.length || !anchorNode || !anchorNode.length) return;
  const visible = elements.filter((ele) => ele.visible());
  const visibleNodes = visible.nodes ? visible.nodes().filter((node) => node.visible()) : visible.filter((node) => node.visible());
  const targetElements = visibleNodes.length ? visibleNodes : visible;
  if (!visible.length) return;
  const container = cy.container();
  const rect = container?.getBoundingClientRect();
  const width = Math.max(1, rect?.width || container?.clientWidth || cy.width());
  const height = Math.max(1, rect?.height || container?.clientHeight || cy.height());
  const padding = options.padding ?? 88;
  const minZoom = options.minZoom ?? 1.02;
  const maxZoom = options.maxZoom ?? 1.18;
  const box = targetElements.renderedBoundingBox({ includeLabels: false });
  const fitZoomX = (width - padding * 2) / Math.max(box.w, 1);
  const fitZoomY = (height - padding * 2) / Math.max(box.h, 1);
  const targetZoom = Math.max(minZoom, Math.min(maxZoom, Math.min(fitZoomX, fitZoomY) * 0.92));
  console.debug("focusElementsOnAnchor", {
    label: options.label || "focus",
    anchorId: anchorNode.id(),
    isFullMapMode,
    width,
    height,
    padding,
    minZoom,
    maxZoom,
    box,
    targetZoom,
    targetPosition: anchorNode.position()
  });
  if (options.animate === false) {
    cy.center(anchorNode);
    cy.zoom(targetZoom);
    return;
  }
  cy.animate(
    { center: { eles: anchorNode }, zoom: targetZoom },
    {
      duration: 360,
      easing: "ease-out-cubic"
    }
  );
}

function fitSelectedCluster() {
  requestCamera({ mode: "fit_cluster", cluster: selectedNode ? selectedNode.data("cluster") : controls.cluster.value, reason: "fit-cluster" });
}

function toggleCompareMode() {
  if (!controls.compareToggle) return;
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
        <p>${anchor.data("cluster") || "No cluster"} · ${anchor.data("year") || "n/a"}</p>
        ${scoreCircles(anchor.data())}
      </div>
      <div class="detail-panel">
        <strong>${candidate.data("title")}</strong>
        <p>${candidate.data("cluster") || "No cluster"} · ${candidate.data("year") || "n/a"}</p>
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
    button.addEventListener("click", () => focusTitleOnGraph(button.dataset.openNode, { forceSelect: true, smooth: true, reason: "detail-jump" }));
  });
  const expandButton = document.querySelector("#details [data-expand-neighborhood]");
  if (expandButton) {
    expandButton.addEventListener("click", () => {
      expandedNeighborhood = !expandedNeighborhood;
      applySelectionHighlight();
      if (selectedNode) {
        requestCamera({ mode: "selected_title_focus", titleId: selectedNode.id(), reason: "expand-neighborhood" });
        showDetails(selectedNode, { keepExpanded: true });
      }
    });
  }
  const focusButton = document.querySelector("#details [data-focus-title]");
  if (focusButton) {
    focusButton.addEventListener("click", () => {
      if (selectedTitleId) focusTitleOnGraph(selectedTitleId, { forceSelect: true, smooth: true, reason: "detail-focus" });
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

function focusTitleOnGraph(titleId, options = {}) {
  const normalizedId = String(titleId);
  if (!graphReady && !options.deferred) {
    userInteractionGeneration += 1;
    userCameraGeneration += 1;
    userHasSelectedTitleInCurrentFilter = true;
    cancelPendingCamera(`pre-ready-selection-${normalizedId}`);
    cancelGraphMotion(`pre-ready-selection-${normalizedId}`);
    pendingFocusRequest = {
      titleId: normalizedId,
      reason: "pending-focus",
      userCameraGeneration
    };
    console.debug("focusTitleOnGraph queued", {
      titleId: normalizedId,
      graphReady,
      browserMode,
      userCameraGeneration,
      userInteractionGeneration
    });
    return false;
  }
  const node = cy.getElementById(normalizedId);
  const found = Boolean(node && node.length);
  const position = found ? node.position() : null;
  const container = cy?.container?.();
  const rect = container?.getBoundingClientRect?.();
  console.debug("focusTitleOnGraph", {
    titleId: normalizedId,
    found,
    graphReady,
    browserMode,
    isLooseMatch: found ? Boolean(Number(node.data("is_outlier") || 0)) : false,
    layoutRunning: Boolean(currentLayout),
    bootIntent: graphBootIntent,
    visible: found ? node.visible() : false,
    x: position?.x,
    y: position?.y,
    viewportWidth: rect?.width || container?.clientWidth || cy?.width?.(),
    viewportHeight: rect?.height || container?.clientHeight || cy?.height?.()
  });
  if (!found) return false;
  userInteractionGeneration += 1;
  userCameraGeneration += 1;
  cancelGraphMotion(`user-selection-${normalizedId}`);
  if (currentLayout && currentLayout.stop) {
    console.debug("layout stop requested", {
      reason: "title-focus",
      titleId: normalizedId,
      generation: layoutRunGeneration
    });
    currentLayout.stop();
    currentLayout = null;
    layoutRunGeneration += 1;
    layoutReady = true;
  }
  selectionGeneration += 1;
  cameraGeneration += 1;
  userHasSelectedTitleInCurrentFilter = true;
  cancelPendingCamera(`user-selection-${normalizedId}`);
  setBrowserMode(browserMode);
  clearTemporaryOutlierPreview();
  selectedOutlierTitleId = null;
  ensureNodeVisible(node);
  if (!options.forceSelect && compareMode && selectedNode && selectedNode.id() !== node.id()) {
    handleNodeTap(node);
    return true;
  }
  showDetails(node);
  requestCamera({ mode: "selected_title_focus", titleId: normalizedId, reason: options.reason || "selection" });
  window.requestAnimationFrame(() => {
    if (selectedTitleId === normalizedId && userHasSelectedTitleInCurrentFilter) {
      cameraGeneration += 1;
      userCameraGeneration += 1;
      cancelPendingCamera(`raf-refocus-${normalizedId}`);
      requestCamera({ mode: "selected_title_focus", titleId: normalizedId, reason: `${options.reason || "selection"}-raf` });
    }
  });
  return true;
}

function openTitleInMapById(nodeId, options = {}) {
  const normalizedId = String(nodeId);
  if (!graphReady && !options.deferred) {
    pendingFocusRequest = {
      titleId: normalizedId,
      reason: "pending-focus",
      userCameraGeneration
    };
    return false;
  }
  const node = cy.getElementById(normalizedId);
  if (!node || !node.length) return false;
  setBrowserMode(browserMode);
  ensureNodeVisible(node);
  if (!options.forceSelect && compareMode && selectedNode && selectedNode.id() !== node.id()) {
    handleNodeTap(node);
    return true;
  }
  showDetails(node);
  return true;
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
  graphBootIntent = {
    mode: "shortcut",
    titleId: "",
    cluster: controls.cluster.value || "",
    shortcut: mode,
    source: "entry-panel"
  };
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
  if (target) focusTitleOnGraph(target.id(), { forceSelect: true, smooth: true, reason: `entry-${mode}` });
}

function showFullMap() {
  if (isFullMapMode) {
    requestCamera({ mode: "full_map", reason: "full-map-refresh" });
    return;
  }
  fullMapPreviousState = {
    zoom: cy.zoom(),
    pan: cy.pan(),
    startupMode,
  };
  isFullMapMode = true;
  clearTemporaryOutlierPreview();
  selectedOutlierTitleId = null;
  startupMode = false;
  controls.entryPanel.hidden = true;
  controls.graphPage.classList.add("is-full-map-mode");
  updateFullMapControls();
  cy.elements().removeClass("startup-focus startup-muted highlight faded");
  applyFilters();
  refreshLabelSet();
  requestCamera({ mode: "full_map", reason: "full-map-enter" });
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
  requestCamera({ mode: selectedTitleId ? "selected_title_focus" : "reset_view", titleId: selectedTitleId, reason: "exit-full-map" });
  renderMovieBrowser();
  setGraphModeNote(selectedNode ? `Focused on ${selectedNode.data("title")}. Explore its neighborhood or ask from this context.` : "Browse the map, then click a title to pull its neighborhood into focus.");
}

function updateFullMapControls() {
  const showExit = Boolean(isFullMapMode);
  const showEnter = !showExit;

  if (controls.showFullMap) {
    controls.showFullMap.hidden = !showEnter;
    controls.showFullMap.classList.toggle("is-hidden", !showEnter);
    controls.showFullMap.setAttribute("aria-hidden", String(!showEnter));
  }

  if (controls.showFullMapOverlay) {
    controls.showFullMapOverlay.hidden = !showEnter;
    controls.showFullMapOverlay.classList.toggle("is-hidden", !showEnter);
    controls.showFullMapOverlay.setAttribute("aria-hidden", String(!showEnter));
  }

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
  const nextMode = ["mapped", "recent", "johnny", "weird", "emotion"].includes(mode) ? mode : "mapped";
  browserMode = nextMode;
  const states = [
    [controls.allMappedTab, nextMode === "mapped"],
    [controls.recentTab, nextMode === "recent"],
    [controls.johnnyTab, nextMode === "johnny"],
    [controls.weirdTab, nextMode === "weird"],
    [controls.emotionTab, nextMode === "emotion"],
  ];
  states.forEach(([button, active]) => {
    if (!button) return;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
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
  askRequestId += 1;
  askExplainRequestId += 1;
  activeAskState = null;
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
  loadSuggestedAsks(selectedTitleId ? Number(selectedTitleId) : null, "reset-ask");
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
