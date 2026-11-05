// SPDX-License-Identifier: LicenseRef-LeanCTX-SDK-Source-1.0
/**
 * Deprecated Engine names (SDK 1.3.0, removed in 2.0). Every alias is the same
 * runtime value as its Instance name, so `instanceof` and `catch` keep working.
 * Error aliases live in `errors.ts`.
 */

import { SubprocessInstanceClient, type InstanceClient, type SubprocessInstanceClientOptions } from "./engine.js";
import {
  InstancePlanningRequest,
  InstanceSource,
  InstanceSourcePlanningClient,
  type InstanceContextPlan,
  type InstanceContextSourceMaterialization,
  type InstanceContextSourceMaterializationV2,
  type InstancePresenterGrant,
  type InstanceSourceDescriptor,
  type InstanceSourceDescriptorInput,
  type InstanceSourcePlan,
  type InstanceSourcePlanResult,
  type InstanceSourceSelection,
} from "./planning.js";
import { InstanceContextClient, type InstanceContextClientOptions, type InstanceContextReadResult } from "./context.js";
import { EnterpriseInstanceClient, type EnterpriseInstanceClientOptions } from "./enterprise.js";
import type { InstanceSourceExecution, InstanceSourceExecutionV2 } from "./source_execution.js";
import { parseInstanceOutcomeResponse, type InstanceOutcomeResponse, type InstanceOutcomeSignal } from "./outcome.js";
import type {
  InstanceProviderExecutionCost,
  InstanceProviderExecutionFailure,
  InstanceProviderExecutionOutput,
  InstanceProviderExecutionResponse,
  InstanceProviderExecutionUsage,
} from "./provider_execution.js";
import { InstanceStatus } from "./protocol.js";

/** @deprecated since 1.3.0, use SubprocessInstanceClient */
export const SubprocessEngineClient = SubprocessInstanceClient;
/** @deprecated since 1.3.0, use SubprocessInstanceClient */
export type SubprocessEngineClient = SubprocessInstanceClient;
/** @deprecated since 1.3.0, use SubprocessInstanceClientOptions */
export type SubprocessEngineClientOptions = SubprocessInstanceClientOptions;
/** @deprecated since 1.3.0, use InstanceClient */
export type EngineClient = InstanceClient;

/** @deprecated since 1.3.0, use InstancePlanningRequest */
export const EnginePlanningRequest = InstancePlanningRequest;
/** @deprecated since 1.3.0, use InstancePlanningRequest */
export type EnginePlanningRequest = InstancePlanningRequest;
/** @deprecated since 1.3.0, use InstanceSource */
export const EngineSource = InstanceSource;
/** @deprecated since 1.3.0, use InstanceSource */
export type EngineSource = InstanceSource;
/** @deprecated since 1.3.0, use InstanceSourcePlanningClient */
export const EngineSourcePlanningClient = InstanceSourcePlanningClient;
/** @deprecated since 1.3.0, use InstanceSourcePlanningClient */
export type EngineSourcePlanningClient = InstanceSourcePlanningClient;
/** @deprecated since 1.3.0, use InstanceContextPlan */
export type EngineContextPlan = InstanceContextPlan;
/** @deprecated since 1.3.0, use InstanceContextSourceMaterialization */
export type EngineContextSourceMaterialization = InstanceContextSourceMaterialization;
/** @deprecated since 1.3.0, use InstanceContextSourceMaterializationV2 */
export type EngineContextSourceMaterializationV2 = InstanceContextSourceMaterializationV2;
/** @deprecated since 1.3.0, use InstancePresenterGrant */
export type EnginePresenterGrant = InstancePresenterGrant;
/** @deprecated since 1.3.0, use InstanceSourceDescriptor */
export type EngineSourceDescriptor = InstanceSourceDescriptor;
/** @deprecated since 1.3.0, use InstanceSourceDescriptorInput */
export type EngineSourceDescriptorInput = InstanceSourceDescriptorInput;
/** @deprecated since 1.3.0, use InstanceSourcePlan */
export type EngineSourcePlan = InstanceSourcePlan;
/** @deprecated since 1.3.0, use InstanceSourcePlanResult */
export type EngineSourcePlanResult = InstanceSourcePlanResult;
/** @deprecated since 1.3.0, use InstanceSourceSelection */
export type EngineSourceSelection = InstanceSourceSelection;

/** @deprecated since 1.3.0, use InstanceContextClient */
export const EngineContextClient = InstanceContextClient;
/** @deprecated since 1.3.0, use InstanceContextClient */
export type EngineContextClient = InstanceContextClient;
/** @deprecated since 1.3.0, use InstanceContextClientOptions */
export type EngineContextClientOptions = InstanceContextClientOptions;
/** @deprecated since 1.3.0, use InstanceContextReadResult */
export type EngineContextReadResult = InstanceContextReadResult;

/** @deprecated since 1.3.0, use EnterpriseInstanceClient */
export const EnterpriseEngineClient = EnterpriseInstanceClient;
/** @deprecated since 1.3.0, use EnterpriseInstanceClient */
export type EnterpriseEngineClient = EnterpriseInstanceClient;
/** @deprecated since 1.3.0, use EnterpriseInstanceClientOptions */
export type EnterpriseEngineClientOptions = EnterpriseInstanceClientOptions;

/** @deprecated since 1.3.0, use InstanceSourceExecution */
export type EngineSourceExecution = InstanceSourceExecution;
/** @deprecated since 1.3.0, use InstanceSourceExecutionV2 */
export type EngineSourceExecutionV2 = InstanceSourceExecutionV2;
/** @deprecated since 1.3.0, use InstanceOutcomeSignal */
export type EngineOutcomeSignal = InstanceOutcomeSignal;
/** @deprecated since 1.3.0, use InstanceOutcomeResponse */
export type EngineOutcomeResponse = InstanceOutcomeResponse;
/** @deprecated since 1.3.0, use parseInstanceOutcomeResponse */
export const parseEngineOutcomeResponse = parseInstanceOutcomeResponse;

/** @deprecated since 1.3.0, use InstanceProviderExecutionOutput */
export type EngineProviderExecutionOutput = InstanceProviderExecutionOutput;
/** @deprecated since 1.3.0, use InstanceProviderExecutionUsage */
export type EngineProviderExecutionUsage = InstanceProviderExecutionUsage;
/** @deprecated since 1.3.0, use InstanceProviderExecutionCost */
export type EngineProviderExecutionCost = InstanceProviderExecutionCost;
/** @deprecated since 1.3.0, use InstanceProviderExecutionFailure */
export type EngineProviderExecutionFailure = InstanceProviderExecutionFailure;
/** @deprecated since 1.3.0, use InstanceProviderExecutionResponse */
export type EngineProviderExecutionResponse = InstanceProviderExecutionResponse;

/** @deprecated since 1.3.0, use InstanceStatus */
export const EngineStatus = InstanceStatus;
/** @deprecated since 1.3.0, use InstanceStatus */
export type EngineStatus = InstanceStatus;
