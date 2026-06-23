CREATE TABLE IF NOT EXISTS jobs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status          TEXT NOT NULL DEFAULT 'QUEUED'
                    CHECK (status IN ('QUEUED','EXTRACTING','CHUNKING',
                                      'GENERATING_AUDIO','MERGING','COMPLETED','FAILED')),
  pdf_path        TEXT NOT NULL,
  mode            TEXT,
  trim_matter     BOOLEAN NOT NULL DEFAULT false,
  result_location TEXT,
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
