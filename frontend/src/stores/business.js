import {loadAll,archiveOverview,archiveBlobs,archiveMigrations,archiveCorrupt} from '../api/business.js';
export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],archive:{overview:{},blobs:[],migrations:[],corrupt:[]}},selected:null};
export async function reload(){
 const [core,overview,blobs,migrations,corrupt]=await Promise.all([loadAll(),archiveOverview(),archiveBlobs(),archiveMigrations(),archiveCorrupt()]);
 store.data={...core,archive:{overview,blobs,migrations,corrupt}};
}
export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
