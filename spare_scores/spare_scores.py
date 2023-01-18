import os
import gzip
import pickle
import logging
import numpy as np
import pandas as pd
from .svm import run_SVC, run_SVR

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def load_model(mdl_path: str):
  with gzip.open(mdl_path, 'rb') as f:
    return pickle.load(f)

def spare_train(df,
                predictors: list,
                to_predict: str,
                pos_group: str = '',
                kernel: str = 'linear',
                save_mdl: bool = True,
                out_path: str = './Mdl',
                mdl_name: str = ''):

  def _expspace(span: list):
    return np.exp(np.linspace(span[0], span[1], num=int(span[1])-int(span[0])+1))

  ################ FILTERS ################
  if not set(predictors).issubset(df.columns):
    return logging.error('Not all predictors exist in the input dataframe.')

  # Determine SPARE type
  if len(df[to_predict].unique()) == 2:
    if pos_group == '':
      return logging.error('"pos_group" not provided (group to assign a positive score).')
    elif pos_group not in df[to_predict].unique():
      return logging.error('"pos_group" does not match one of the two groups in variable to predict.')
    if np.min(df[to_predict].value_counts()) < 10:
      return logging.error('At least one of the groups to classify is too small (n<10).')
    elif np.min(df[to_predict].value_counts()) < 100:
      logging.warn('At least one of the groups to classify may be too small to build a robust SPARE classification model (n<100).')
    if np.sum((df['PTID']+df[to_predict]).duplicated()) > 0:
      logging.warn('Training dataset has duplicate participants.')
    spare_type = 'classification'
    groups_to_classify = [a for a in df[to_predict].unique() if a != pos_group] + [pos_group]
  elif len(df[to_predict].unique()) > 2:
    if df[to_predict].dtype not in ['int64', 'float64']:
      return logging.error('Variable to predict must be either binary or numeric.')
    if len(df.index) < 10:
      return logging.error('Sample size is too small (n<10).')
    elif len(df.index) < 100:
      logging.warn('Sample size may be too small to build a robust SPARE regression model (n<100).')
    if np.sum(df['PTID'].duplicated()) > 0:
      logging.warn('Training dataset has duplicate participants.')
    if pos_group != '':
      logging.info('SPARE regression model does not need a "pos_group". This will be ignored.')
    spare_type = 'regression'
  else:
    return logging.error('Variable to predict has no variance.')

  if to_predict in predictors:
    logging.info('Variable to predict is in the predictor set. This will be removed from the set.')
    predictors.remove(to_predict)
  if np.sum(np.sum(pd.isna(df[predictors]))) > 0:
    logging.info('Some participants have invalid predictor variables (such as n/a). They will be excluded from the training set.')
    df = df.loc[np.sum(pd.isna(df[predictors]), axis=1) == 0].reset_index(drop=True)
  #########################################

  # Initiate SPARE model
  metaData = {'spare_type': spare_type,
              'kernel':kernel,
              'n': len(df.index),
              'age_range': np.floor([np.min(df['Age']), np.max(df['Age'])]),
              'to_predict': to_predict,
              'predictors': predictors}

  # Convert categorical variables
  var_categorical = df[predictors].dtypes == np.object
  var_categorical = var_categorical[var_categorical].index
  metaData['categorical_var_map'] = dict(zip(var_categorical, [None]*len(var_categorical)))
  for var in var_categorical:
    if len(df[var].unique()) == 2:
      metaData['categorical_var_map'][var] = {df[var].unique()[0]: 1, df[var].unique()[1]: 2}
      df[var] = df[var].map(metaData['categorical_var_map'][var])

  # SPARE classification
  if spare_type == 'classification':
    if kernel == 'linear':
      metaData['pos_group'] = pos_group
      param_grid = {'C': _expspace([-9, 5])}
    elif kernel == 'rbf':
      param_grid = {'C': _expspace([-9, 5]), 'gamma': _expspace([-5, 5])}
    if len(df.index) > 1000:
      _, _, _, params = run_SVC(df.sample(n=500, random_state=2022).reset_index(drop=True), predictors,
                to_predict, groups_to_classify, param_grid=param_grid, kernel=kernel, n_repeats=1, verbose=0)
      for par in param_grid.keys():
        param_grid[par] = _expspace([np.min(params[f'{par}_optimal']), np.max(params[f'{par}_optimal'])])
    df['predicted'], mdl, metaData['auc'], metaData['params'] = run_SVC(
                df, predictors, to_predict, groups_to_classify, param_grid=param_grid, kernel=kernel)

  # SPARE regression
  elif spare_type == 'regression':
    param_grid = {'C': _expspace([-5, 5]), 'epsilon': _expspace([-5, 5])}
    if len(df.index) > 1000:
      _, _, _, params = run_SVR(df.sample(n=500, random_state=2022).reset_index(drop=True), predictors,
                to_predict, param_grid=param_grid, n_repeats=1, verbose=0)
      for par in param_grid.keys():
        param_grid[par] = _expspace([np.min(params[f'{par}_optimal']), np.max(params[f'{par}_optimal'])])
    df['predicted'], mdl, metaData['mae'], metaData['params'] = run_SVR(
                  df, predictors, to_predict, param_grid=param_grid)
  metaData['cv_results'] = df[list(dict.fromkeys(['PTID', 'Age', 'Sex', to_predict, 'predicted']))]

  # Save model
  if save_mdl:
    if mdl_name == '':
      to_predict_ = to_predict.replace('.', '_')
      mdl_name = f'SPARE_{spare_type}_{to_predict_}'
    with gzip.open(f'{out_path}/mdl_{mdl_name}.pkl.gz', 'wb') as f:
      pickle.dump((mdl, metaData), f)

  return mdl, metaData


def spare_test(df,
               mdl_path: str,
               save_csv: bool = False,
               out_path: str = './Out'):

  # Load trained SPARE model
  mdl, metaData = load_model(mdl_path)
  df = df.copy()

  ################ FILTERS ################
  if not set(metaData['predictors']).issubset(df.columns):
    cols_not_found = sorted(set(metaData['predictors']) - set(df.columns))
    if len([a for a in cols_not_found if '_' not in a]) > 0:
      return logging.error(f'Not all predictors exist in the input dataframe: {cols_not_found}')
    try:
      roi_name = [a for a in metaData['predictors'] if '_' in a]
      for roi_alter in [[int(a.split('_')[-1]) for a in roi_name],
                        [a.split('_')[-1] for a in roi_name],
                        ['R'+a.split('_')[-1] for a in roi_name]]:  
        if set(roi_alter).issubset(df.columns):
          df = df.rename(columns=dict(zip(roi_alter, roi_name)))
          logging.info(f'ROI names changed to match the model (e.g. {roi_alter[0]} to {roi_name[0]}).')
          continue
    except Exception:
      return logging.error(f'Not all predictors exist in the input dataframe: {cols_not_found}')
    cols_not_found = sorted(set(metaData['predictors']) - set(df.columns))
    if len(cols_not_found) > 0:
      return logging.error(f'Not all predictors exist in the input dataframe: {cols_not_found}')

  if (np.min(df['Age']) < metaData['age_range'][0]) or (np.max(df['Age']) > metaData['age_range'][1]):
    logging.warn('Some participants fall outside of the age range of the SPARE model.')

  if np.sum(np.sum(pd.isna(df[metaData['predictors']]))) > 0:
    logging.warn('Some participants have invalid predictor variables.')

  if np.any(df['PTID'].isin(metaData['cv_results']['PTID'])):
    n_training = int(np.sum(df['PTID'].isin(metaData['cv_results']['PTID'])))
    logging.info(f'{n_training} participants have matching IDs to IDs from the training sample. Only models where they were left out from the training will be used for testing.')
  #########################################

  # Convert categorical variables
  if 'categorical_var_map' in metaData.keys():
    for var in metaData['categorical_var_map'].keys():
      if isinstance(metaData['categorical_var_map'][var], dict):
        if np.all(df[var].isin(metaData['categorical_var_map'][var].keys())):
          df[var] = df[var].map(metaData['categorical_var_map'][var])
        else:
          expected_var = list(metaData['categorical_var_map'][var].keys())
          return logging.error(f'Column "{var}" contains value(s) other than expected: {expected_var}')

  # Output model description
  print('Model Info: training N =', metaData['n'], end=' / ')
  print('ages =', int(metaData['age_range'][0]), '-', int(metaData['age_range'][1]), end=' / ')
  if metaData['spare_type'] == 'classification':
    print('expected AUC =', np.round(np.mean(metaData['auc']), 3))
  elif metaData['spare_type'] == 'regression':
    print('expected MAE =', np.round(np.mean(metaData['mae']), 3))

  # Calculate SPARE scores
  n_ensemble = len(mdl['scaler'])
  ss = np.zeros([len(df.index), n_ensemble])
  for i in range(n_ensemble):
    X = mdl['scaler'][i].transform(df[metaData['predictors']])
    if metaData['kernel'] == 'linear':
      ss[:, i] = np.sum(X * mdl['mdl'][i].coef_, axis=1) + mdl['mdl'][i].intercept_
    else:
      ss[:, i] = mdl['mdl'][i].decision_function(X)
    if metaData['spare_type'] == 'regression':
      ss[:, i] = (ss[:, i] - mdl['bias_correct']['int'][i]) / mdl['bias_correct']['slope'][i]
    ss[df['PTID'].isin(metaData['cv_results']['PTID'][mdl['cv_folds'][i][0]]), i] = np.nan

  df_results = pd.DataFrame(data={'SPARE_scores': np.nanmean(ss, axis=1)})

  # Save results csv
  if save_csv:
    mdl_name = mdl_path.split('/')[-1].split('.')[0]
    if not os.path.exists(out_path):
      os.makedirs(out_path)
    df_results.to_csv(f'{out_path}/SPAREs_from_{mdl_name}.csv')

  return df_results