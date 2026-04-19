/**
 * ATSchecker v2 — Frontend Application
 * SPA-style navigation, API integration, onboarding wizard
 */

// ─── State ──────────────────────────────────────────────────
const state = {
  currentPage: 'dashboard',
  profile: null,
  jobs: [],
  matches: [],
  applications: [],
  wizardStep: 1,
  jobsPage: 1,
  jobsPerPage: 25,
  jobFilter: 'all',
  jobSearch: '',
  jobSort: 'score',
};

// ─── API Helpers ────────────────────────────────────────────
async function api(url, options = {}) {
  try {
    const resp = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    return await resp.json();
  } catch (err) {
    console.error(`API error: ${url}`, err);
    throw err;
  }
}

// ─── Toast ──────────────────────────────────────────────────
function toast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  const icon = type === 'success' ? '&#10003;' : '&#9888;';
  el.innerHTML = `<span>${icon}</span> ${message}`;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
}

// ─── Page Navigation ────────────────────────────────────────
function switchPage(page) {
  state.currentPage = page;
  document.querySelectorAll('.page-content').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('.nav-links a').forEach(a => a.classList.remove('active'));

  const pageEl = document.getElementById(`page-${page}`);
  const navEl = document.getElementById(`nav-${page}`);
  if (pageEl) pageEl.classList.remove('hidden');
  if (navEl) navEl.classList.add('active');

  // Load data for the page
  if (page === 'jobs') loadJobs();
  if (page === 'pipeline') loadPipeline();
  if (page === 'profile') loadProfile();
  if (page === 'dashboard') loadDashboard();
}

// ─── Dashboard ──────────────────────────────────────────────
async function loadDashboard() {
  try {
    const [summary, matchData] = await Promise.all([
      api('/api/pipeline-summary'),
      api('/api/matches?limit=8'),
    ]);

    // Stats
    document.getElementById('stat-total-jobs').textContent = summary.total_jobs || 0;
    document.getElementById('stat-ats').textContent = summary.ats_score || '--';

    const pipeline = summary.pipeline || {};
    const applied = (pipeline.applied || 0) + (pipeline.interview || 0) + (pipeline.offer || 0);
    document.getElementById('stat-applied').textContent = applied;

    // Top matches
    const matches = matchData.matches || [];
    const strongCount = matches.filter(m => m.overall_score >= 55).length;
    document.getElementById('stat-matches').textContent = strongCount || '--';

    const listEl = document.getElementById('top-matches-list');
    if (matches.length === 0) {
      listEl.innerHTML = `<div class="empty-state"><div class="empty-icon">&#128270;</div><p>No matches yet. Run a job search first!</p></div>`;
      return;
    }

    listEl.innerHTML = matches.slice(0, 6).map(m => {
      const scoreClass = m.overall_score >= 75 ? 'high' : m.overall_score >= 35 ? 'medium' : 'low';
      const skills = (m.matched_skills || []).slice(0, 4).map(s => `<span class="skill-tag">${esc(s)}</span>`).join('');
      return `
        <div class="job-card">
          <div class="score-ring ${scoreClass}">${Math.round(m.overall_score)}</div>
          <div class="job-info">
            <div class="job-title"><a href="${esc(m.url)}" target="_blank">${esc(m.job_title)}</a></div>
            <div class="job-meta">
              <span>${esc(m.company)}</span>
              <span>${esc(m.location)}</span>
              <span>${esc(m.source)}</span>
            </div>
            ${skills ? `<div class="job-skills">${skills}</div>` : ''}
          </div>
          <div class="job-actions">
            <button class="btn btn-ghost btn-sm" onclick="saveJob(${m.job_id || 0})" title="Save">&#128278;</button>
          </div>
        </div>`;
    }).join('');

    // Skills gap
    loadSkillsGap(matchData.gap_analysis);
  } catch (err) {
    console.error('Dashboard load error:', err);
  }
}

function loadSkillsGap(gap) {
  const el = document.getElementById('skills-gap-content');
  if (!gap || !gap.top_missing || gap.top_missing.length === 0) {
    el.innerHTML = '<p class="text-sm text-muted">Complete your profile and run a search to see skills gap analysis.</p>';
    return;
  }
  const items = gap.top_missing.slice(0, 8).map(skill => {
    const freq = gap.missing_skills_frequency[skill] || 0;
    const pct = Math.min(100, freq * 5);
    return `
      <div class="mb-md">
        <div class="flex justify-between text-sm mb-sm">
          <span>${esc(skill)}</span>
          <span class="text-muted">${freq} jobs</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" style="width: ${pct}%"></div>
        </div>
      </div>`;
  }).join('');
  el.innerHTML = items;
}

// ─── Jobs Page ──────────────────────────────────────────────
async function loadJobs() {
  try {
    const data = await api(`/api/matches?limit=200`);
    state.matches = data.matches || [];
    renderJobs();
  } catch {
    document.getElementById('jobs-list').innerHTML = '<div class="empty-state"><p>Failed to load jobs</p></div>';
  }
}

function renderJobs() {
  let filtered = [...state.matches];

  // Apply filter
  if (state.jobFilter === 'high') filtered = filtered.filter(m => m.overall_score >= 75);
  else if (state.jobFilter === 'medium') filtered = filtered.filter(m => m.overall_score >= 35 && m.overall_score < 75);
  else if (state.jobFilter === 'saved') filtered = filtered.filter(m => m.saved);

  // Apply search
  if (state.jobSearch) {
    const q = state.jobSearch.toLowerCase();
    filtered = filtered.filter(m =>
      (m.job_title || '').toLowerCase().includes(q) ||
      (m.company || '').toLowerCase().includes(q) ||
      (m.matched_skills || []).some(s => s.toLowerCase().includes(q))
    );
  }

  // Apply sort
  if (state.jobSort === 'score') filtered.sort((a, b) => b.overall_score - a.overall_score);
  else if (state.jobSort === 'date') filtered.sort((a, b) => (b.posted_date || '').localeCompare(a.posted_date || ''));
  else if (state.jobSort === 'company') filtered.sort((a, b) => (a.company || '').localeCompare(b.company || ''));

  // Pagination
  const start = (state.jobsPage - 1) * state.jobsPerPage;
  const page = filtered.slice(start, start + state.jobsPerPage);

  document.getElementById('jobs-showing').textContent = `Showing ${start + 1}-${Math.min(start + state.jobsPerPage, filtered.length)} of ${filtered.length} jobs`;
  document.getElementById('jobs-prev').disabled = state.jobsPage <= 1;
  document.getElementById('jobs-next').disabled = start + state.jobsPerPage >= filtered.length;

  const listEl = document.getElementById('jobs-list');
  if (page.length === 0) {
    listEl.innerHTML = '<div class="empty-state"><div class="empty-icon">&#128270;</div><p>No jobs match your filters</p></div>';
    return;
  }

  listEl.innerHTML = page.map(m => {
    const scoreClass = m.overall_score >= 75 ? 'high' : m.overall_score >= 35 ? 'medium' : 'low';
    const matchedSkills = (m.matched_skills || []).slice(0, 5).map(s => `<span class="skill-tag">${esc(s)}</span>`).join('');
    const missingSkills = (m.missing_skills || []).slice(0, 3).map(s => `<span class="skill-tag missing">${esc(s)}</span>`).join('');
    const reasons = (m.match_reasons || []).slice(0, 2).map(r => esc(r)).join(' &middot; ');
    const warnings = (m.warnings || []).map(w => `<span class="badge badge-warning">${esc(w)}</span>`).join(' ');

    return `
      <div class="job-card">
        <div class="score-ring ${scoreClass}">${Math.round(m.overall_score)}</div>
        <div class="job-info">
          <div class="job-title"><a href="${esc(m.url)}" target="_blank">${esc(m.job_title)}</a></div>
          <div class="job-meta">
            <span>${esc(m.company)}</span>
            <span>${esc(m.location)}</span>
            <span class="badge badge-info">${esc(m.source)}</span>
            ${m.posted_date ? `<span>${esc(m.posted_date.split('T')[0])}</span>` : ''}
          </div>
          <div class="job-skills mt-sm">${matchedSkills}${missingSkills}</div>
          ${reasons ? `<div class="text-xs text-muted mt-sm">${reasons}</div>` : ''}
          ${warnings ? `<div class="mt-sm">${warnings}</div>` : ''}
        </div>
        <div class="job-actions">
          <button class="btn btn-accent btn-sm" onclick="saveJob(${m.job_id || 0})" title="Save to pipeline">&#128278;</button>
          <button class="btn btn-ghost btn-sm" onclick="dismissJob(${m.job_id || 0})" title="Not interested">&#10006;</button>
        </div>
      </div>`;
  }).join('');
}

// ─── Pipeline ───────────────────────────────────────────────
async function loadPipeline() {
  try {
    const data = await api('/api/applications');
    state.applications = data.applications || [];
    renderPipeline();
  } catch {
    toast('Failed to load pipeline', 'error');
  }
}

function renderPipeline() {
  const statuses = ['saved', 'applied', 'interview', 'offer', 'rejected'];
  let total = 0;

  statuses.forEach(status => {
    const items = state.applications.filter(a => a.status === status);
    total += items.length;

    document.getElementById(`count-${status}`).textContent = items.length;

    const col = document.getElementById(`col-${status}`);
    if (items.length === 0) {
      col.innerHTML = '<div class="text-xs text-muted text-center" style="padding: 16px;">No items</div>';
      return;
    }

    col.innerHTML = items.map(app => `
      <div class="pipeline-item" onclick="showAppDetail(${app.id})">
        <div class="truncate" style="font-weight: 500;">${esc(app.title)}</div>
        <div class="text-xs text-muted">${esc(app.company)}</div>
      </div>
    `).join('');
  });

  document.getElementById('pipeline-total').textContent = `${total} applications`;
}

// ─── Profile Page ───────────────────────────────────────────
async function loadProfile() {
  try {
    const data = await api('/api/profile');
    state.profile = data;

    // Fill form
    document.getElementById('profile-name').value = data.name || '';
    document.getElementById('profile-email').value = data.email || '';
    document.getElementById('profile-german').value = data.german_level || 'A2';
    document.getElementById('profile-experience').value = data.experience_level || 'entry';

    // CV status
    const cvStatus = document.getElementById('cv-status-text');
    if (cvStatus) {
      const p = data.profile || {};
      if (p.cv_file_name) {
        cvStatus.textContent = `Current: ${p.cv_file_name}`;
        cvStatus.style.color = 'var(--accent)';
      } else if (data.skills && data.skills.length > 0) {
        cvStatus.textContent = `${data.skills.length} skills loaded from CV`;
        cvStatus.style.color = 'var(--accent)';
      }
    }

    // Roles chips — merge options with any custom roles already saved
    const roleOptions = data.role_options || [];
    const savedRoles = data.desired_roles || [];
    const allRoles = [...new Set([...roleOptions, ...savedRoles])];
    renderChips('roles-chips', allRoles, savedRoles);

    // Locations chips — merge options with any custom locations already saved
    const cityOptions = data.city_options || [];
    const savedLocations = data.desired_locations || [];
    const allLocations = [...new Set([...cityOptions, ...savedLocations])];
    renderChips('locations-chips', allLocations, savedLocations);

    // Types chips — merge options with any custom types
    const typeOptions = data.type_options || [];
    const savedTypes = data.desired_types || [];
    const allTypes = [...new Set([...typeOptions, ...savedTypes])];
    renderChips('types-chips', allTypes, savedTypes);

    // Skills
    const skills = data.skills || [];
    document.getElementById('skills-count').textContent = `${skills.length} skills detected`;
    const skillsEl = document.getElementById('skills-chips');
    skillsEl.innerHTML = skills.map(s => {
      const name = typeof s === 'string' ? s : (s.name || s.canonical || '');
      return `<button class="chip selected" onclick="toggleChip(this)">${esc(name)}</button>`;
    }).join('');
  } catch {
    toast('Failed to load profile', 'error');
  }
}

async function saveProfile() {
  try {
    const selectedRoles = getSelectedChips('roles-chips');
    const selectedLocations = getSelectedChips('locations-chips');
    const selectedTypes = getSelectedChips('types-chips');
    const selectedSkills = getSelectedChips('skills-chips');

    // Build skills array for saving
    const skillDicts = selectedSkills.map(s => ({ name: s, confirmed: true }));

    await api('/api/profile', {
      method: 'PUT',
      body: JSON.stringify({
        name: document.getElementById('profile-name').value,
        email: document.getElementById('profile-email').value,
        german_level: document.getElementById('profile-german').value,
        experience_level: document.getElementById('profile-experience').value,
        desired_roles: selectedRoles,
        desired_locations: selectedLocations,
        desired_types: selectedTypes,
        skills: skillDicts,
      }),
    });
    toast('Profile saved!');
  } catch {
    toast('Failed to save profile', 'error');
  }
}

// ─── Chips Helper ───────────────────────────────────────────
function renderChips(containerId, options, selected) {
  const container = document.getElementById(containerId);
  const selectedSet = new Set(Array.isArray(selected) ? selected : []);
  container.innerHTML = options.map(opt => {
    const isSelected = selectedSet.has(opt);
    return `<button class="chip ${isSelected ? 'selected' : ''}" onclick="toggleChip(this)">${esc(opt)}</button>`;
  }).join('');
}

function toggleChip(el) {
  el.classList.toggle('selected');
}

function getSelectedChips(containerId) {
  const chips = document.querySelectorAll(`#${containerId} .chip.selected`);
  return Array.from(chips).map(c => c.textContent.trim());
}

function addCustomChip(containerId, inputId) {
  const input = document.getElementById(inputId);
  const value = (input.value || '').trim();
  if (!value) return;

  const container = document.getElementById(containerId);
  // Check if already exists
  const existing = Array.from(container.querySelectorAll('.chip')).map(c => c.textContent.trim().toLowerCase());
  if (existing.includes(value.toLowerCase())) {
    toast('Already in the list', 'error');
    return;
  }

  const chip = document.createElement('button');
  chip.className = 'chip selected';
  chip.textContent = value;
  chip.onclick = () => toggleChip(chip);
  container.appendChild(chip);
  input.value = '';
  toast(`Added "${value}"`);
}

// ─── Profile CV Upload ─────────────────────────────────────
function setupProfileCVUpload() {
  const zone = document.getElementById('profile-cv-upload-zone');
  const input = document.getElementById('profile-cv-file-input');
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) uploadProfileCV(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => { if (input.files.length) uploadProfileCV(input.files[0]); });
}

async function uploadProfileCV(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    toast('Please upload a PDF file', 'error');
    return;
  }

  document.getElementById('profile-cv-upload-zone').style.display = 'none';
  document.getElementById('profile-cv-upload-status').classList.remove('hidden');

  const formData = new FormData();
  formData.append('cv', file);

  try {
    const resp = await fetch('/api/profile/upload-cv', { method: 'POST', body: formData });
    const data = await resp.json();

    document.getElementById('profile-cv-upload-status').classList.add('hidden');
    const result = document.getElementById('profile-cv-upload-result');
    result.classList.remove('hidden');

    if (data.status === 'success') {
      const newSkills = (data.skills || data.extracted?.skills || []);
      const skillTags = newSkills.slice(0, 10).map(s => {
        const name = typeof s === 'string' ? s : (s.name || '');
        return `<span class="skill-tag">${esc(name)}</span>`;
      }).join('');
      result.innerHTML = `
        <div class="card" style="background: rgba(16,185,129,0.05); border-color: rgba(16,185,129,0.2);">
          <h4 class="text-accent">&#10003; CV Updated Successfully</h4>
          <p class="text-sm text-muted mt-sm">${newSkills.length} skills extracted. Your profile has been updated.</p>
          <div class="job-skills mt-sm">${skillTags}</div>
        </div>`;
      // Refresh the skills display
      loadProfile();
      toast('CV uploaded and skills refreshed!');
    } else {
      result.innerHTML = `<div class="card" style="border-color: var(--danger);"><p>Failed: ${esc(data.error || 'Unknown error')}</p></div>`;
      document.getElementById('profile-cv-upload-zone').style.display = '';
    }
  } catch {
    document.getElementById('profile-cv-upload-status').classList.add('hidden');
    document.getElementById('profile-cv-upload-zone').style.display = '';
    toast('Upload failed', 'error');
  }
}

// ─── Job Actions ────────────────────────────────────────────
async function saveJob(jobId) {
  if (!jobId) { toast('Cannot save: no job ID', 'error'); return; }
  try {
    await api('/api/applications', { method: 'POST', body: JSON.stringify({ job_id: jobId, status: 'saved' }) });
    toast('Job saved to pipeline!');
  } catch { toast('Failed to save', 'error'); }
}

async function dismissJob(jobId) {
  if (!jobId) return;
  try {
    await api('/api/jobs/dismiss', { method: 'POST', body: JSON.stringify({ job_id: jobId }) });
    toast('Job dismissed');
    state.matches = state.matches.filter(m => m.job_id !== jobId);
    renderJobs();
  } catch { toast('Failed to dismiss', 'error'); }
}

function showAppDetail(appId) {
  // TODO: modal with notes, status change
  toast(`Application #${appId} detail coming soon`);
}

// ─── Scraping ───────────────────────────────────────────────
async function startScraping() {
  const btn = document.getElementById('btn-scrape');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Scraping...';

  try {
    const resp = await fetch('/compile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });

    if (resp.status === 409) {
      toast('Scraping is already running, please wait...', 'error');
      // Still poll for completion
    } else if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    } else {
      toast('Job search started! This may take a few minutes...');
    }

    // Poll for scrape completion
    let attempts = 0;
    const maxAttempts = 120; // up to 6 minutes
    while (attempts < maxAttempts) {
      await new Promise(r => setTimeout(r, 3000));
      attempts++;
      try {
        const status = await api('/api/status');
        const scrapeStatus = status.scrape || {};
        if (!scrapeStatus.running) {
          // Scraping finished
          toast('Job search complete! Refreshing...');
          break;
        }
        // Update button with progress
        const msg = scrapeStatus.message || 'Scraping...';
        btn.innerHTML = `<div class="spinner"></div> ${msg}`;
      } catch { break; }
    }

    // Refresh dashboard data
    const summary = await api('/api/pipeline-summary');
    document.getElementById('stat-total-jobs').textContent = summary.total_jobs || 0;
    document.getElementById('job-count-badge').textContent = `${summary.total_jobs} jobs`;
    loadDashboard();

  } catch (err) {
    toast('Failed to start scraping: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '&#128269; Scrape';
  }
}

// ─── Onboarding Wizard ──────────────────────────────────────
function showWizard() {
  document.getElementById('onboarding-wizard').classList.remove('hidden');
}

function hideWizard() {
  document.getElementById('onboarding-wizard').classList.add('hidden');
}

function wizardBack() {
  if (state.wizardStep > 1) {
    state.wizardStep--;
    updateWizardUI();
  }
}

async function wizardNext() {
  if (state.wizardStep === 1) {
    // Validate CV uploaded
    const result = document.getElementById('cv-upload-result');
    if (result.classList.contains('hidden')) {
      toast('Please upload your CV first', 'error');
      return;
    }
    state.wizardStep = 2;
    await loadWizardStep2();
  } else if (state.wizardStep === 2) {
    state.wizardStep = 3;
    await loadWizardStep3();
  } else if (state.wizardStep === 3) {
    await completeOnboarding();
    return;
  }
  updateWizardUI();
}

function updateWizardUI() {
  // Update step indicators
  document.querySelectorAll('.wizard-step').forEach(el => {
    const step = parseInt(el.dataset.step);
    el.classList.remove('active', 'done');
    if (step === state.wizardStep) el.classList.add('active');
    else if (step < state.wizardStep) el.classList.add('done');
  });

  // Show/hide step bodies
  for (let i = 1; i <= 3; i++) {
    const body = document.getElementById(`wizard-step-${i}`);
    if (body) body.classList.toggle('hidden', i !== state.wizardStep);
  }

  // Back button
  document.getElementById('wizard-back').disabled = state.wizardStep <= 1;

  // Next button text
  const nextBtn = document.getElementById('wizard-next');
  nextBtn.innerHTML = state.wizardStep === 3 ? '&#10003; Complete Setup' : 'Next &#8594;';
  if (state.wizardStep === 3) nextBtn.classList.add('btn-accent');
  else nextBtn.classList.remove('btn-accent');
}

async function loadWizardStep2() {
  try {
    const data = await api('/api/profile');
    const skills = data.skills || [];
    const container = document.getElementById('wizard-skills-chips');
    container.innerHTML = skills.map(s => {
      const name = typeof s === 'string' ? s : (s.name || '');
      return `<button class="chip selected" onclick="toggleChip(this)">${esc(name)}</button>`;
    }).join('');

    if (skills.length === 0) {
      container.innerHTML = '<p class="text-muted">No skills detected yet. You can add them manually on the profile page.</p>';
    }
  } catch { /* ignore */ }
}

async function loadWizardStep3() {
  try {
    const data = await api('/api/profile');
    renderChips('wizard-roles-chips', data.role_options || [], data.desired_roles || []);
    renderChips('wizard-locations-chips', (data.city_options || []).slice(0, 20), data.desired_locations || []);
    renderChips('wizard-types-chips', data.type_options || [], data.desired_types || []);
  } catch { /* ignore */ }
}

async function completeOnboarding() {
  const btn = document.getElementById('wizard-next');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Saving...';

  try {
    const roles = getSelectedChips('wizard-roles-chips');
    const locations = getSelectedChips('wizard-locations-chips');
    const types = getSelectedChips('wizard-types-chips');
    const skills = getSelectedChips('wizard-skills-chips');

    await api('/api/profile/complete-onboarding', {
      method: 'POST',
      body: JSON.stringify({
        desired_roles: roles,
        desired_locations: locations,
        desired_types: types,
        confirmed_skills: skills,
        german_level: document.getElementById('wizard-german').value,
        experience_level: document.getElementById('wizard-experience').value,
      }),
    });

    toast('Profile setup complete! Welcome to ATSchecker &#127881;');
    hideWizard();
    loadDashboard();
  } catch {
    toast('Failed to save profile', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '&#10003; Complete Setup';
  }
}

// ─── CV Upload ──────────────────────────────────────────────
function setupCVUpload() {
  const zone = document.getElementById('cv-upload-zone');
  const input = document.getElementById('cv-file-input');
  if (!zone || !input) return;

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) uploadCV(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => { if (input.files.length) uploadCV(input.files[0]); });
}

async function uploadCV(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    toast('Please upload a PDF file', 'error');
    return;
  }

  document.getElementById('cv-upload-zone').classList.add('hidden');
  document.getElementById('cv-upload-status').classList.remove('hidden');

  const formData = new FormData();
  formData.append('cv', file);

  try {
    const resp = await fetch('/api/profile/upload-cv', { method: 'POST', body: formData });
    const data = await resp.json();

    document.getElementById('cv-upload-status').classList.add('hidden');
    const result = document.getElementById('cv-upload-result');
    result.classList.remove('hidden');

    if (data.status === 'success') {
      const skills = (data.skills || []).slice(0, 8).map(s => `<span class="skill-tag">${esc(typeof s === 'string' ? s : s.name)}</span>`).join('');
      result.innerHTML = `
        <div class="card" style="background: rgba(16,185,129,0.05); border-color: rgba(16,185,129,0.2);">
          <h4 class="text-accent">&#10003; CV Analyzed Successfully</h4>
          <p class="text-sm text-muted mt-sm">${data.skills ? data.skills.length : 0} skills detected</p>
          <div class="job-skills mt-sm">${skills}</div>
        </div>`;
    } else {
      result.innerHTML = `<div class="card" style="border-color: var(--danger);"><p>Failed: ${esc(data.error || 'Unknown error')}</p></div>`;
      document.getElementById('cv-upload-zone').classList.remove('hidden');
    }
  } catch {
    document.getElementById('cv-upload-status').classList.add('hidden');
    document.getElementById('cv-upload-zone').classList.remove('hidden');
    toast('Upload failed', 'error');
  }
}

// ─── Event Listeners ────────────────────────────────────────
function setupEventListeners() {
  // Nav links
  document.querySelectorAll('.nav-links a').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const page = link.dataset.page;
      if (page) switchPage(page);
    });
  });

  // Job search
  const searchInput = document.getElementById('job-search-input');
  if (searchInput) {
    let debounce;
    searchInput.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => { state.jobSearch = searchInput.value; state.jobsPage = 1; renderJobs(); }, 300);
    });
  }

  // Job sort
  const sortSelect = document.getElementById('job-sort-select');
  if (sortSelect) {
    sortSelect.addEventListener('change', () => { state.jobSort = sortSelect.value; renderJobs(); });
  }

  // Filter chips
  document.querySelectorAll('#job-filters .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('#job-filters .chip').forEach(c => c.classList.remove('selected'));
      chip.classList.add('selected');
      state.jobFilter = chip.dataset.filter;
      state.jobsPage = 1;
      renderJobs();
    });
  });

  // Pagination
  document.getElementById('jobs-prev')?.addEventListener('click', () => { state.jobsPage--; renderJobs(); });
  document.getElementById('jobs-next')?.addEventListener('click', () => { state.jobsPage++; renderJobs(); });
}

// ─── Utility ────────────────────────────────────────────────
function esc(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// Backward compat: safe quit
function safeQuit() {
  if (confirm('Shut down the server?')) {
    fetch('/quit', { method: 'POST' }).then(() => {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;"><h2>Server stopped. You can close this tab.</h2></div>';
    });
  }
}

// ─── Init ───────────────────────────────────────────────────
async function init() {
  setupEventListeners();
  setupCVUpload();
  setupProfileCVUpload();

  // Check if onboarding needed
  try {
    const profile = await api('/api/profile');
    state.profile = profile;
    if (!profile.onboarding_complete) {
      showWizard();

      // If CV is already loaded (skills detected), auto-advance past step 1
      if (profile.skills && profile.skills.length > 0) {
        // Show success on step 1
        document.getElementById('cv-upload-zone').classList.add('hidden');
        const result = document.getElementById('cv-upload-result');
        result.classList.remove('hidden');
        const skills = profile.skills.slice(0, 8).map(s => {
          const name = typeof s === 'string' ? s : (s.name || '');
          return `<span class="skill-tag">${esc(name)}</span>`;
        }).join('');
        result.innerHTML = `
          <div class="card" style="background: rgba(16,185,129,0.05); border-color: rgba(16,185,129,0.2);">
            <h4 class="text-accent">&#10003; CV Already Loaded</h4>
            <p class="text-sm text-muted mt-sm">${profile.skills.length} skills detected from your CV</p>
            <div class="job-skills mt-sm">${skills}</div>
          </div>`;
      }
    }
  } catch { /* ignore */ }

  // Load dashboard
  loadDashboard();
}

// Expose functions globally
window._app = { switchPage, startScraping, wizardBack, wizardNext, saveProfile, showWizard, addCustomChip };

document.addEventListener('DOMContentLoaded', init);
