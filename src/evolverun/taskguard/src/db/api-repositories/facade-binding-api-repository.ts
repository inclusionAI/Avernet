/** COMMUNITY STUB: facade-binding-api-repository.ts is internal-only. Corp extensions provide real implementation. */
import { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";

export class FacadeBindingApiRepository extends FacadeBindingRepository {
  constructor(..._args: any[]) {
    super(null as any);
  }
  async listAll(..._args: any[]): Promise<any[]> {
    return super.listAll();
  }
}
