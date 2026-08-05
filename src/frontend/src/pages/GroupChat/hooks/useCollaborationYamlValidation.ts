import * as BcnController from '@/services/backend-api/BcnController';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useEffect, useRef, useState } from 'react';

export function useCollaborationYamlValidation() {
  const [isValidatingCollaborationYaml, setIsValidatingCollaborationYaml] =
    useState(false);
  const validationRequestIdRef = useRef(0);

  const cancelCollaborationYamlValidation = useCallback(() => {
    validationRequestIdRef.current += 1;
    setIsValidatingCollaborationYaml(false);
  }, []);

  const validateCollaborationYaml = useCallback(
    async (
      definitionYaml: string,
    ): Promise<BcnController.CollaborationDefinitionValidationResponse | null> => {
      const requestId = validationRequestIdRef.current + 1;
      validationRequestIdRef.current = requestId;
      setIsValidatingCollaborationYaml(true);

      try {
        const response =
          await BcnController.validateCollaborationDefinitionYaml({
            definition_yaml: definitionYaml,
          });
        return validationRequestIdRef.current === requestId ? response : null;
      } catch (error) {
        if (validationRequestIdRef.current !== requestId) {
          return null;
        }
        console.error(
          '[useCollaborationYamlValidation] Failed to validate YAML:',
          error,
        );
        throw new Error(extractErrorMessage(error, 'YAML 校验请求失败'));
      } finally {
        if (validationRequestIdRef.current === requestId) {
          setIsValidatingCollaborationYaml(false);
        }
      }
    },
    [],
  );

  useEffect(
    () => () => {
      validationRequestIdRef.current += 1;
    },
    [],
  );

  return {
    validateCollaborationYaml,
    cancelCollaborationYamlValidation,
    isValidatingCollaborationYaml,
  };
}
