import schema from "./agent-contract.json";
import type { AgentRun } from "./agent";
type ApiFields = keyof typeof schema.properties;
type ExactFields = [Exclude<ApiFields, keyof AgentRun>, Exclude<keyof AgentRun, ApiFields>] extends [never, never] ? true : false;
export const agentRunFieldsMatchOpenApi: ExactFields = true;
