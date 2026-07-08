import { gql } from "@apollo/client";

export function loadUserPlan() {
  return gql`
    query UserPlan {
      user {
        id
        planTier
      }
    }
  `;
}
