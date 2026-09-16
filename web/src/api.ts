/** Job lifecycle statuses returned by the Go API. */
export type JobStatus =
  | "QUEUED"
  | "EXTRACTING"
  | "CHUNKING"
  | "GENERATING_AUDIO"
  | "MERGING"
  | "COMPLETED"
  | "FAILED";

export interface Job {
  id: string;
  status: JobStatus;
  mode: string | null;
  trim_matter: boolean;
  result_location: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateJobResponse {
  id: string;
  status: JobStatus;
}

/**
 * Thin client for the existing Go job API.
 * Uses relative URLs by default so Vite/nginx proxies work same-origin.
 */
export class JobsApi {
  static base(): string {
    const configured = import.meta.env.VITE_API_BASE;
    if (configured !== undefined && configured !== "") {
      return configured.replace(/\/$/, "");
    }
    return "";
  }

  static async createJob(
    file: File,
    mode: "structured" | "narrative",
    trimMatter: boolean,
  ): Promise<CreateJobResponse> {
    const body = new FormData();
    body.append("file", file);
    body.append("mode", mode);
    if (trimMatter) {
      body.append("trim_matter", "true");
    }

    const res = await fetch(`${JobsApi.base()}/jobs`, {
      method: "POST",
      body,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `Upload failed (${res.status})`);
    }
    return (await res.json()) as CreateJobResponse;
  }

  static async getJob(id: string): Promise<Job> {
    const res = await fetch(`${JobsApi.base()}/jobs/${id}`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `Status check failed (${res.status})`);
    }
    return (await res.json()) as Job;
  }

  static resultUrl(id: string): string {
    return `${JobsApi.base()}/jobs/${id}/result`;
  }
}

export class JobPoller {
  static readonly INTERVAL_MS = 2000;

  static statusLabel(status: JobStatus): string {
    const labels: Record<JobStatus, string> = {
      QUEUED: "Queued — waiting for a worker",
      EXTRACTING: "Extracting text from the PDF",
      CHUNKING: "Chunking text for narration",
      GENERATING_AUDIO: "Synthesising speech",
      MERGING: "Merging audio into one MP3",
      COMPLETED: "Ready to listen",
      FAILED: "Conversion failed",
    };
    return labels[status];
  }

  static progressPercent(status: JobStatus): number {
    const order: JobStatus[] = [
      "QUEUED",
      "EXTRACTING",
      "CHUNKING",
      "GENERATING_AUDIO",
      "MERGING",
      "COMPLETED",
    ];
    if (status === "FAILED") return 100;
    const idx = order.indexOf(status);
    return Math.round((idx / (order.length - 1)) * 100);
  }
}
