-- plan_inquiries: 플랜 업그레이드 문의 테이블
CREATE TABLE IF NOT EXISTS plan_inquiries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    desired_plan VARCHAR(20) NOT NULL CHECK (desired_plan IN ('starter', 'pro', 'enterprise')),
    message TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_inquiries_user_id ON plan_inquiries(user_id);
CREATE INDEX IF NOT EXISTS idx_plan_inquiries_status ON plan_inquiries(status);
