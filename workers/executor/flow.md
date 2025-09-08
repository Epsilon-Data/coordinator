1. Exectuor fetch job where status is "ai_approved"
2. Loads repo code from shared folder
3. And check build folder and archetype
4. Change csv file in archetype/{dataset_id}/*.csv with real dataset (but for now use any csv). dataset_id can be found in build/*.yml file
5. And encrypt that realdataset csv fiel with kms, file name must be same what it was in archetype/{dataset_id}/*.csv
6. Then zip whole repo folder and encrypt it with kms
7. Then in EnclaveApp load that zip file and decrypt it and unzip it (so for now we need EnclaveAppLocal and EnclaveApp)
8. After unzip decrypt dataset which is in archetype/{dataset_id}/*.csv
9. Then run script in build folder where is given script filename in build/*.yml file