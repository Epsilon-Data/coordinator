-- Production-like base schema BEFORE the release-gate migration.
-- = drizzle-kit export of ResearchWorkspace origin/main (942a289) shared/schema.ts
--   + columns added by coordinator alembic revisions 44be9b128f17 and 2e1ea7116ba4 (prod is at 2e1ea7116ba4).

CREATE TABLE "job_logs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_id" varchar(255) NOT NULL,
	"worker_name" varchar(100) NOT NULL,
	"step_name" varchar(255) NOT NULL,
	"step_type" varchar(50) NOT NULL,
	"level" varchar(20) DEFAULT 'info' NOT NULL,
	"message" text NOT NULL,
	"log_metadata" jsonb,
	"progress" integer,
	"duration_ms" integer,
	"error_details" jsonb,
	"parent_log_id" uuid,
	"created_at" timestamp DEFAULT now()
);

CREATE TABLE "job_requests" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"job_id" varchar(255) NOT NULL,
	"workspace_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"commit_sha" varchar(255) NOT NULL,
	"commit_message" text,
	"commit_author" varchar(255),
	"commit_date" timestamp,
	"status" varchar(50) DEFAULT 'queued' NOT NULL,
	"started_at" timestamp,
	"completed_at" timestamp,
	"duration_seconds" integer,
	"exit_code" integer,
	"error_message" text,
	"logs" text,
	"output_files" jsonb,
	"result_metadata" text,
	"execution_output" text,
	"execution_error" text,
	"execution_method" varchar(100),
	"validation_status" varchar(50),
	"validation_policy" text,
	"validation_decision" text,
	"attestation" text,
	"ai_logs" text,
	"ai_enabled" boolean DEFAULT true NOT NULL,
	"zkp_enabled" boolean DEFAULT true NOT NULL,
	"execution_metrics" text,
	"verification_receipt" text,
	"enclave_version" text,
	"enclave_pcr0" text,
	"created_at" timestamp DEFAULT now(),
	"updated_at" timestamp DEFAULT now(),
	CONSTRAINT "job_requests_job_id_unique" UNIQUE("job_id")
);

CREATE TABLE "users" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"external_id" varchar(255) NOT NULL,
	"username" varchar(255) NOT NULL,
	"email" varchar(255) NOT NULL,
	"full_name" varchar(255),
	"avatar_url" text,
	"role" varchar(50) DEFAULT 'user' NOT NULL,
	"approval_status" varchar(50) DEFAULT 'pending' NOT NULL,
	"institution" varchar(255),
	"research_purpose" text,
	"expected_duration" varchar(100),
	"github_access_token" text,
	"github_username" varchar(255),
	"created_at" timestamp DEFAULT now(),
	"updated_at" timestamp DEFAULT now(),
	"approved_at" timestamp,
	"approved_by" uuid,
	CONSTRAINT "users_external_id_unique" UNIQUE("external_id")
);

CREATE TABLE "workspaces" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(255) NOT NULL,
	"description" text,
	"user_id" uuid NOT NULL,
	"github_repo" varchar(255) NOT NULL,
	"github_branch" varchar(255) NOT NULL,
	"status" varchar(50) DEFAULT 'active' NOT NULL,
	"created_at" timestamp DEFAULT now(),
	"updated_at" timestamp DEFAULT now(),
	"last_commit_sha" varchar(255),
	"last_commit_message" text,
	"last_commit_author" varchar(255),
	"last_commit_date" timestamp
);

ALTER TABLE "job_logs" ADD CONSTRAINT "job_logs_job_id_job_requests_job_id_fk" FOREIGN KEY ("job_id") REFERENCES "public"."job_requests"("job_id") ON DELETE cascade ON UPDATE no action;
ALTER TABLE "job_requests" ADD CONSTRAINT "job_requests_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE cascade ON UPDATE no action;
ALTER TABLE "job_requests" ADD CONSTRAINT "job_requests_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;
ALTER TABLE "workspaces" ADD CONSTRAINT "workspaces_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;

-- coordinator alembic 44be9b128f17
ALTER TABLE job_requests ADD COLUMN job_id_committed varchar;
ALTER TABLE job_requests ADD COLUMN researcher_nonce varchar;
CREATE INDEX ix_job_requests_job_id_committed ON job_requests (job_id_committed);
-- coordinator alembic 2e1ea7116ba4
ALTER TABLE job_requests ADD COLUMN commitment_receipt text;
ALTER TABLE job_requests ADD COLUMN ha_receipt text;
