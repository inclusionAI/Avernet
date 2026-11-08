import userEvent from '@testing-library/user-event'
import {render,screen,waitFor} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
const api=vi.hoisted(()=>({material:vi.fn()}))
vi.mock('../../../../api/repair-batches',()=>({repairBatches:api}))
import FrozenRepairEvidence from '../FrozenRepairEvidence'
import type {RepairItem,RepairRevision} from '../../../../../server/contracts/repair-batch'
it('loads the original frozen revision only when expanded and never substitutes live evidence',async()=>{
 const item={itemId:'one',context:{excerpt:'core excerpt'}} as unknown as RepairItem
 const revision={taskId:'task',revision:2,input:{materials:{recordId:'material'},items:[item]}} as unknown as RepairRevision
 api.material.mockResolvedValueOnce({...item,context:{proof:'complete frozen report'}})
 const {unmount}=render(<FrozenRepairEvidence revision={revision} item={item}/>);
 expect(api.material).not.toHaveBeenCalled();
 await userEvent.click(screen.getByText('原始建议与证据'));
 expect(await screen.findByText(/complete frozen report/)).toBeVisible();
 expect(api.material).toHaveBeenCalledWith('task',2,'one');unmount();
 api.material.mockRejectedValueOnce(new Error('missing'))
 render(<FrozenRepairEvidence revision={revision} item={item}/>);
 await userEvent.click(screen.getByText('原始建议与证据'));
 await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('未使用实时数据替代'));
 expect(screen.queryByText(/complete frozen report/)).not.toBeInTheDocument();
});
