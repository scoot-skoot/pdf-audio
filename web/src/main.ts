import "./styles.css";
import { Job, JobPoller, JobsApi, JobStatus } from "./api";

class App {
  private root: HTMLElement;
  private file: File | null = null;
  private pollTimer: number | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.render();
    this.bind();
  }

  private render(): void {
    this.root.innerHTML = `
      <div class="shell">
        <header class="topbar">
          <a class="brand-mark" href="/">pdf<span>-to-audio</span></a>
          <p class="topbar-note">PDF in · narrated MP3 out</p>
        </header>

        <section class="hero" id="hero">
          <div class="hero-copy">
            <h1 class="brand-hero">pdf<em>-to-audio</em></h1>
            <p class="hero-lead">
              Drop a PDF, choose structured or narrative narration, and listen as the
              conversion finishes — then play or download the MP3.
            </p>
            <div class="cta-row">
              <button class="btn btn-primary" type="button" id="focus-upload">Choose a PDF</button>
              <button class="btn btn-ghost" type="button" id="scroll-options">Set mode first</button>
            </div>
          </div>

          <aside class="hero-panel" id="upload-panel">
            <label class="dropzone" id="dropzone" for="pdf-input">
              <input id="pdf-input" type="file" accept="application/pdf,.pdf" />
              <div>
                <p class="drop-title">Drop your PDF here</p>
                <p class="drop-meta">or click to browse — max practical size ~64&nbsp;MB</p>
                <p class="file-name" id="file-name" hidden></p>
              </div>
            </label>

            <div class="options" id="options">
              <div>
                <span class="field-label">Mode</span>
                <div class="mode-toggle" role="radiogroup" aria-label="Conversion mode">
                  <label class="mode-option">
                    <input type="radio" name="mode" value="structured" checked />
                    <span>Structured</span>
                  </label>
                  <label class="mode-option">
                    <input type="radio" name="mode" value="narrative" />
                    <span>Narrative</span>
                  </label>
                </div>
              </div>

              <label class="checkbox-row">
                <input id="trim-matter" type="checkbox" />
                <span>
                  Trim front/back matter
                  <br />
                  <small>Uses the LLM when configured; otherwise falls back safely.</small>
                </span>
              </label>
            </div>

            <div class="submit-row">
              <button class="btn btn-primary" type="button" id="convert-btn" disabled>
                Convert to audiobook
              </button>
            </div>
          </aside>
        </section>

        <section class="workspace" id="workspace" aria-live="polite">
          <div class="workspace-header">
            <div>
              <h2>Conversion</h2>
              <p class="job-id" id="job-id"></p>
            </div>
            <button class="btn btn-ghost" type="button" id="new-job-btn">Start over</button>
          </div>

          <div class="status-panel">
            <div class="status-line">
              <p class="status-text" id="status-text">Preparing…</p>
              <span class="status-pct" id="status-pct">0%</span>
            </div>
            <div class="progress" id="progress"><span id="progress-bar"></span></div>
            <div class="error-box" id="error-box" hidden></div>
            <div class="player-panel" id="player-panel">
              <audio id="player" controls preload="metadata"></audio>
              <a class="btn btn-primary" id="download-link" href="#" download>Download MP3</a>
            </div>
          </div>
        </section>
      </div>
    `;
  }

  private el<T extends HTMLElement>(id: string): T {
    const node = this.root.querySelector(`#${id}`);
    if (!node) {
      throw new Error(`Missing #${id}`);
    }
    return node as T;
  }

  private bind(): void {
    const dropzone = this.el<HTMLLabelElement>("dropzone");
    const input = this.el<HTMLInputElement>("pdf-input");
    const convertBtn = this.el<HTMLButtonElement>("convert-btn");
    const focusUpload = this.el<HTMLButtonElement>("focus-upload");
    const scrollOptions = this.el<HTMLButtonElement>("scroll-options");
    const newJobBtn = this.el<HTMLButtonElement>("new-job-btn");

    focusUpload.addEventListener("click", () => input.click());
    scrollOptions.addEventListener("click", () => {
      this.el("options").scrollIntoView({ behavior: "smooth", block: "center" });
    });

    input.addEventListener("change", () => {
      const file = input.files?.[0] ?? null;
      this.setFile(file);
    });

    ["dragenter", "dragover"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.remove("is-dragover");
      });
    });
    dropzone.addEventListener("drop", (e) => {
      const dt = (e as DragEvent).dataTransfer;
      const file = dt?.files?.[0] ?? null;
      if (file) {
        this.setFile(file);
      }
    });

    convertBtn.addEventListener("click", () => {
      void this.startConversion();
    });
    newJobBtn.addEventListener("click", () => this.reset());
  }

  private setFile(file: File | null): void {
    if (file && !file.name.toLowerCase().endsWith(".pdf")) {
      this.flashError("Please choose a .pdf file.");
      return;
    }
    this.file = file;
    const name = this.el<HTMLParagraphElement>("file-name");
    const dropzone = this.el<HTMLLabelElement>("dropzone");
    const convertBtn = this.el<HTMLButtonElement>("convert-btn");

    if (file) {
      name.hidden = false;
      name.textContent = file.name;
      dropzone.classList.add("has-file");
      convertBtn.disabled = false;
    } else {
      name.hidden = true;
      name.textContent = "";
      dropzone.classList.remove("has-file");
      convertBtn.disabled = true;
    }
  }

  private flashError(message: string): void {
    window.alert(message);
  }

  private selectedMode(): "structured" | "narrative" {
    const checked = this.root.querySelector<HTMLInputElement>('input[name="mode"]:checked');
    return checked?.value === "narrative" ? "narrative" : "structured";
  }

  private async startConversion(): Promise<void> {
    if (!this.file) {
      return;
    }

    const convertBtn = this.el<HTMLButtonElement>("convert-btn");
    convertBtn.disabled = true;
    convertBtn.textContent = "Uploading…";

    try {
      const trimMatter = this.el<HTMLInputElement>("trim-matter").checked;
      const created = await JobsApi.createJob(this.file, this.selectedMode(), trimMatter);
      this.showWorkspace(created.id, created.status);
      this.startPolling(created.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.showWorkspace("(upload failed)", "FAILED");
      this.setFailed(message);
    } finally {
      convertBtn.disabled = !this.file;
      convertBtn.textContent = "Convert to audiobook";
    }
  }

  private showWorkspace(jobId: string, status: JobStatus | "FAILED"): void {
    this.el("hero").classList.add("hidden-hero");
    this.el("workspace").classList.add("is-visible");
    this.el<HTMLParagraphElement>("job-id").textContent = `Job ${jobId}`;
    this.applyStatus(status as JobStatus);
  }

  private startPolling(jobId: string): void {
    this.stopPolling();
    const tick = async () => {
      try {
        const job = await JobsApi.getJob(jobId);
        this.applyJob(job);
        if (job.status === "COMPLETED" || job.status === "FAILED") {
          this.stopPolling();
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        this.setFailed(message);
        this.stopPolling();
      }
    };
    void tick();
    this.pollTimer = window.setInterval(() => {
      void tick();
    }, JobPoller.INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollTimer !== null) {
      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private applyJob(job: Job): void {
    this.applyStatus(job.status);
    if (job.status === "FAILED") {
      this.setFailed(job.error || "Unknown conversion error");
      return;
    }
    if (job.status === "COMPLETED") {
      this.showPlayer(job.id);
    }
  }

  private applyStatus(status: JobStatus): void {
    const pct = JobPoller.progressPercent(status);
    this.el<HTMLParagraphElement>("status-text").textContent = JobPoller.statusLabel(status);
    this.el<HTMLSpanElement>("status-pct").textContent = `${pct}%`;
    this.el<HTMLElement>("progress-bar").style.width = `${pct}%`;
    this.el("progress").classList.toggle("is-failed", status === "FAILED");
    if (status !== "FAILED") {
      this.el("error-box").hidden = true;
    }
    if (status !== "COMPLETED") {
      this.el("player-panel").classList.remove("is-visible");
    }
  }

  private setFailed(message: string): void {
    this.applyStatus("FAILED");
    const box = this.el<HTMLDivElement>("error-box");
    box.hidden = false;
    box.textContent = message;
  }

  private showPlayer(jobId: string): void {
    const url = JobsApi.resultUrl(jobId);
    const player = this.el<HTMLAudioElement>("player");
    const download = this.el<HTMLAnchorElement>("download-link");
    player.src = url;
    download.href = url;
    download.download = `${jobId}.mp3`;
    this.el("player-panel").classList.add("is-visible");
  }

  private reset(): void {
    this.stopPolling();
    this.el("workspace").classList.remove("is-visible");
    this.el("hero").classList.remove("hidden-hero");
    this.el("player-panel").classList.remove("is-visible");
    this.el("error-box").hidden = true;
    this.el<HTMLAudioElement>("player").removeAttribute("src");
    const input = this.el<HTMLInputElement>("pdf-input");
    input.value = "";
    this.setFile(null);
  }
}

const mount = document.querySelector<HTMLElement>("#app");
if (mount) {
  new App(mount);
}
